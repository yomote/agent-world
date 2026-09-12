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
      throw new Error("github_operations_are_native_only");
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
