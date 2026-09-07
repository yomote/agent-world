// 信頼済みCodex transportが既存connectorを一度だけ呼ぶ。credentialや承認回答を扱わない。
export async function relay(request, tools) {
  const args = request.arguments;
  const repo = "yomote/agent-world";
  const base = `https://api.github.com/repos/${repo}`;
  const direct = {
    create_draft_pr: "create_pull_request",
    post_review_evidence: "add_comment_to_issue",
    ready_pr: "mark_pull_request_ready_for_review",
    review_threads: "list_pull_request_review_threads",
    normal_merge: "merge_pull_request",
    create_helper_issue: "create_issue",
    close_issue: "update_issue",
  };
  let result;
  if (Object.hasOwn(direct, request.operation)) {
    if ((args.repository_full_name ?? args.repo_full_name) !== repo) {
      throw new Error("repository_not_allowed");
    }
    result = await tools[`mcp__codex_apps__github_${direct[request.operation]}`](args);
  } else {
    let url;
    if (request.operation === "current_ci" && /^[0-9a-f]{40}$/.test(args.commit_sha)) {
      url = `${base}/actions/workflows/ci.yml/runs?event=pull_request&head_sha=${args.commit_sha}&per_page=100`;
    } else if (request.operation === "main_protection") {
      url = `${base}/branches/main`;
    } else if (request.operation === "review_comments" && Number.isInteger(args.number)) {
      url = `${base}/issues/${args.number}/comments?per_page=100`;
    } else if (request.operation === "pr_info" && Number.isInteger(args.pr_number)) {
      url = `${base}/pulls/${args.pr_number}`;
    } else {
      throw new Error("owner_operation_or_unknown_operation");
    }
    result = await tools.mcp__codex_apps__github_fetch({ url });
  }
  if (result.isError) {
    return { id: request.id, status: "unknown", result: { tool_error: true } };
  }
  let data = result.structuredContent;
  if (data?.result) data = data.result;
  if (typeof data?.content === "string") data = JSON.parse(data.content);
  if (data === undefined) {
    return { id: request.id, status: "unknown", result: { response_missing: true } };
  }
  return { id: request.id, status: "ok", result: data };
}

const quote = (value) => `'${String(value).replaceAll("'", "''")}'`;

// Codex tool hostで明示的に1要求を配送する。OS常駐・再試行loopは作らない。
// formal reviewだけは同じread-only reviewerへの依頼をhostへ返し、実回答後にcompleteを呼ぶ。
export async function pump(root, campaign, tools) {
  if (!["runner", "billing-helper"].includes(campaign)) throw new Error("campaign_not_allowed");
  const claimed = await tools.exec_command({
    cmd: `.venv/Scripts/python.exe -m scripts.automation.bridge ${campaign} take`,
    workdir: root,
    max_output_tokens: 2500,
  });
  if (claimed.exit_code !== 0) throw new Error("claim_failed");
  const request = JSON.parse(claimed.output);
  if (request === null) return null;
  if (request.operation === "independent_review") return { pending_review: request };
  let response;
  try {
    if (request.operation === "current_check") {
      const args = request.arguments;
      const directory = `${root}/artifacts/self-improvement/${campaign}`;
      let run = await tools.exec_command({
        cmd: `.venv/Scripts/python.exe -m scripts.automation.local_check ${quote(args.workspace)} ${quote(args.head)} ${quote(directory)}`,
        workdir: root,
        sandbox_permissions: "require_escalated",
        justification: "runnerが要求した固定headのnpm run checkを既存ローカル環境で実行します。",
        yield_time_ms: 1000,
        max_output_tokens: 2500,
      });
      let output = run.output;
      while (run.session_id !== undefined) {
        run = await tools.write_stdin({
          session_id: run.session_id,
          chars: "",
          yield_time_ms: 1000,
          max_output_tokens: 2500,
        });
        output += run.output;
      }
      response = {
        id: request.id,
        status: run.exit_code === 0 ? "ok" : "unknown",
        result: run.exit_code === 0 ? JSON.parse(output) : { local_check_transport_failed: true },
      };
    } else {
      response = await relay(request, tools);
    }
  } catch {
    response = { id: request.id, status: "unknown", result: { transport_exception: true } };
  }
  await complete(root, request, response, tools);
  return { operation: request.operation, response };
}

export async function complete(root, request, response, tools) {
  if (!/^[0-9a-f-]{36}$/.test(request.id) || response.id !== request.id)
    throw new Error("response_identity");
  if (!["runner", "billing-helper"].includes(request.campaign))
    throw new Error("campaign_not_allowed");
  const path = `${root}/artifacts/self-improvement/${request.campaign}/transport/${request.id}.response.json`;
  const patch = `*** Begin Patch\n*** Add File: ${path}\n+${JSON.stringify(response)}\n*** End Patch`;
  const result = await tools.apply_patch(patch);
  return result;
}
