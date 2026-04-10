// ── Eval runner — executes scenarios against the bot ────────

import * as http from "http";
import * as https from "https";
import { URL } from "url";
import { CONFIG } from "./config";
import { EvalScenario, EvalStep, EvalCheck } from "./scenarios";
import { judgeResponse, JudgeVerdict } from "./judge";

// ── Types ───────────────────────────────────────────────────

export interface CheckResult {
  check_id: string;
  description: string;
  type: EvalCheck["type"];
  severity: EvalCheck["severity"];
  passed: boolean;
  reason: string;
}

export interface StepResult {
  step_index: number;
  description: string;
  user_message: string;
  bot_response: string;
  latency_ms: number;
  checks: CheckResult[];
  all_passed: boolean;
}

export interface RunResult {
  run_index: number;
  steps: StepResult[];
  all_passed: boolean;
}

export interface ScenarioResult {
  scenario_id: string;
  scenario_name: string;
  runs: RunResult[];
  total_runs: number;
  passed_runs: number;
  failed_runs: number;
  pass_rate: number;
}

// ── Bot API client ──────────────────────────────────────────

function httpRequest(
  url: string,
  options: http.RequestOptions,
  body?: string,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const mod = parsedUrl.protocol === "https:" ? https : http;

    const req = mod.request(parsedUrl, options, (res) => {
      let data = "";
      res.on("data", (chunk: Buffer) => (data += chunk.toString()));
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}: ${data}`));
        } else {
          resolve(data);
        }
      });
    });

    req.on("error", reject);
    req.setTimeout(CONFIG.requestTimeout, () => {
      req.destroy(new Error("Request timeout"));
    });
    if (body) req.write(body);
    req.end();
  });
}

async function botChat(message: string, sessionId: string): Promise<string> {
  const url = `${CONFIG.botEndpoint}/chat`;
  const payload = JSON.stringify({ message, session_id: sessionId });

  const raw = await httpRequest(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  }, payload);

  const resp = JSON.parse(raw);
  return resp.reply || "";
}

async function botReset(sessionId: string): Promise<void> {
  const url = `${CONFIG.botEndpoint}/session/reset`;
  const payload = JSON.stringify({ session_id: sessionId });

  try {
    await httpRequest(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }, payload);
  } catch {
    // Some endpoints may not support reset — that's ok
  }
}

// ── Check evaluation ────────────────────────────────────────

function evaluateContains(response: string, check: EvalCheck): CheckResult {
  const pattern = check.pattern!;
  let found: boolean;

  if (pattern instanceof RegExp) {
    found = pattern.test(response);
  } else {
    found = response.toLowerCase().includes(pattern.toLowerCase());
  }

  return {
    check_id: check.id,
    description: check.description,
    type: check.type,
    severity: check.severity,
    passed: found,
    reason: found ? "Pattern found" : `Pattern not found: ${pattern}`,
  };
}

function evaluateNotContains(response: string, check: EvalCheck): CheckResult {
  const pattern = check.pattern!;
  let found: boolean;

  if (pattern instanceof RegExp) {
    found = pattern.test(response);
  } else {
    found = response.toLowerCase().includes(pattern.toLowerCase());
  }

  return {
    check_id: check.id,
    description: check.description,
    type: check.type,
    severity: check.severity,
    passed: !found,
    reason: !found ? "Pattern not found (good)" : `Unwanted pattern found: ${pattern}`,
  };
}

async function evaluateJudge(
  history: { role: string; content: string }[],
  userMessage: string,
  botResponse: string,
  check: EvalCheck,
): Promise<CheckResult> {
  const verdict: JudgeVerdict = await judgeResponse(
    history,
    userMessage,
    botResponse,
    check.criteria!,
  );

  return {
    check_id: check.id,
    description: check.description,
    type: check.type,
    severity: check.severity,
    passed: verdict.pass,
    reason: verdict.reason,
  };
}

// ── Run single scenario ─────────────────────────────────────

async function runScenarioOnce(
  scenario: EvalScenario,
  runIndex: number,
  criticalOnly: boolean,
): Promise<RunResult> {
  const sessionId = `eval_${scenario.id}_run${runIndex}_${Date.now()}`;
  await botReset(sessionId);

  const history: { role: string; content: string }[] = [];
  const steps: StepResult[] = [];

  for (let si = 0; si < scenario.steps.length; si++) {
    const step = scenario.steps[si];

    // Reset if needed
    if (step.reset_before) {
      await botReset(sessionId);
      history.length = 0;
    }

    // Send message
    let botResponse: string;
    const startedAt = Date.now();
    let latencyMs = 0;
    try {
      botResponse = await botChat(step.user_message, sessionId);
      latencyMs = Date.now() - startedAt;
    } catch (err: any) {
      latencyMs = Date.now() - startedAt;
      steps.push({
        step_index: si,
        description: step.description,
        user_message: step.user_message,
        bot_response: `ERROR: ${err.message}`,
        latency_ms: latencyMs,
        checks: [
          {
            check_id: "BOT_ERROR",
            description: "Bot responded",
            type: "contains",
            severity: "critical",
            passed: false,
            reason: `Bot error: ${err.message}`,
          },
        ],
        all_passed: false,
      });
      break;
    }

    // Filter checks if critical-only mode
    let checks = step.checks;
    if (criticalOnly) {
      checks = checks.filter((c) => c.severity === "critical");
      if (checks.length === 0) {
        // No critical checks in this step — skip
        history.push({ role: "user", content: step.user_message });
        history.push({ role: "assistant", content: botResponse });
        continue;
      }
    }

    // Evaluate checks
    const checkResults: CheckResult[] = [];
    for (const check of checks) {
      let result: CheckResult;

      if (check.type === "contains") {
        result = evaluateContains(botResponse, check);
      } else if (check.type === "not_contains") {
        result = evaluateNotContains(botResponse, check);
      } else {
        result = await evaluateJudge(history, step.user_message, botResponse, check);
      }

      checkResults.push(result);
    }

    const allPassed = checkResults.every((c) => c.passed);

    steps.push({
      step_index: si,
      description: step.description,
      user_message: step.user_message,
      bot_response: botResponse,
      latency_ms: latencyMs,
      checks: checkResults,
      all_passed: allPassed,
    });

    // Update history
    history.push({ role: "user", content: step.user_message });
    history.push({ role: "assistant", content: botResponse });

    // Delay between steps
    if (CONFIG.stepDelay > 0 && si < scenario.steps.length - 1) {
      await new Promise((r) => setTimeout(r, CONFIG.stepDelay));
    }
  }

  return {
    run_index: runIndex,
    steps,
    all_passed: steps.every((s) => s.all_passed),
  };
}

// ── Run scenario N times ────────────────────────────────────

export async function runScenario(
  scenario: EvalScenario,
  repeatOverride?: number,
  criticalOnly: boolean = false,
): Promise<ScenarioResult> {
  const repeat = repeatOverride ?? scenario.repeat;
  const runs: RunResult[] = [];

  for (let i = 0; i < repeat; i++) {
    process.stdout.write(`  Run ${i + 1}/${repeat}...`);
    const run = await runScenarioOnce(scenario, i, criticalOnly);
    runs.push(run);
    const status = run.all_passed ? "PASS" : "FAIL";
    process.stdout.write(` ${status}\n`);
  }

  const passedRuns = runs.filter((r) => r.all_passed).length;

  return {
    scenario_id: scenario.id,
    scenario_name: scenario.name,
    runs,
    total_runs: repeat,
    passed_runs: passedRuns,
    failed_runs: repeat - passedRuns,
    pass_rate: passedRuns / repeat,
  };
}
