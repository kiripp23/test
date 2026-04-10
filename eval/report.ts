// ── Report generation ───────────────────────────────────────

import * as fs from "fs";
import * as path from "path";
import { ScenarioResult, CheckResult } from "./runner";
import { CONFIG } from "./config";

export interface LatencyStats {
  count: number;
  min_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
}

export interface EvalReport {
  run_date: string;
  judge_model: string;
  total_scenarios: number;
  total_runs: number;
  summary: Record<
    string,
    {
      runs: number;
      passed: number;
      failed: number;
      pass_rate: number;
      latency: LatencyStats;
    }
  >;
  latency_overall: LatencyStats;
  failures: {
    scenario: string;
    run: number;
    step: number;
    step_description: string;
    check_id: string;
    user_message: string;
    bot_response: string;
    reason: string;
  }[];
  critical_checks: Record<string, { passed: number; total: number; pass_rate: number }>;
  threshold_results: {
    critical: { threshold: number; actual: number; passed: boolean };
    core: { threshold: number; actual: number; passed: boolean };
    nice_to_have: { threshold: number; actual: number; passed: boolean };
  };
}

function computeLatency(samples: number[]): LatencyStats {
  if (samples.length === 0) {
    return { count: 0, min_ms: 0, avg_ms: 0, p50_ms: 0, p95_ms: 0, max_ms: 0 };
  }
  const sorted = [...samples].sort((a, b) => a - b);
  const sum = sorted.reduce((a, b) => a + b, 0);
  const pick = (p: number) =>
    sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
  return {
    count: sorted.length,
    min_ms: sorted[0],
    avg_ms: Math.round(sum / sorted.length),
    p50_ms: pick(0.5),
    p95_ms: pick(0.95),
    max_ms: sorted[sorted.length - 1],
  };
}

export function buildReport(results: ScenarioResult[]): EvalReport {
  const summary: EvalReport["summary"] = {};
  const failures: EvalReport["failures"] = [];
  const criticalMap: Record<string, { passed: number; total: number }> = {};
  const overallLatencies: number[] = [];

  // Aggregate severity stats
  const severityStats = { critical: { p: 0, t: 0 }, core: { p: 0, t: 0 }, nice_to_have: { p: 0, t: 0 } };

  for (const sr of results) {
    const scenarioLatencies: number[] = [];
    for (const run of sr.runs) {
      for (const step of run.steps) {
        if (typeof step.latency_ms === "number" && step.latency_ms > 0) {
          scenarioLatencies.push(step.latency_ms);
          overallLatencies.push(step.latency_ms);
        }
      }
    }

    summary[sr.scenario_id] = {
      runs: sr.total_runs,
      passed: sr.passed_runs,
      failed: sr.failed_runs,
      pass_rate: Math.round(sr.pass_rate * 1000) / 1000,
      latency: computeLatency(scenarioLatencies),
    };

    for (const run of sr.runs) {
      for (const step of run.steps) {
        for (const check of step.checks) {
          // Track by severity
          const sev = check.severity as keyof typeof severityStats;
          if (severityStats[sev]) {
            severityStats[sev].t++;
            if (check.passed) severityStats[sev].p++;
          }

          // Track critical checks by ID
          if (check.severity === "critical") {
            if (!criticalMap[check.check_id]) {
              criticalMap[check.check_id] = { passed: 0, total: 0 };
            }
            criticalMap[check.check_id].total++;
            if (check.passed) criticalMap[check.check_id].passed++;
          }

          // Collect failures
          if (!check.passed) {
            failures.push({
              scenario: sr.scenario_id,
              run: run.run_index,
              step: step.step_index,
              step_description: step.description,
              check_id: check.check_id,
              user_message: step.user_message,
              bot_response: step.bot_response,
              reason: check.reason,
            });
          }
        }
      }
    }
  }

  const criticalChecks: EvalReport["critical_checks"] = {};
  for (const [id, stats] of Object.entries(criticalMap)) {
    criticalChecks[id] = {
      ...stats,
      pass_rate: stats.total > 0 ? Math.round((stats.passed / stats.total) * 1000) / 1000 : 1,
    };
  }

  const calcRate = (s: { p: number; t: number }) => (s.t > 0 ? s.p / s.t : 1);

  const thresholdResults: EvalReport["threshold_results"] = {
    critical: {
      threshold: CONFIG.thresholds.critical,
      actual: Math.round(calcRate(severityStats.critical) * 1000) / 1000,
      passed: calcRate(severityStats.critical) >= CONFIG.thresholds.critical,
    },
    core: {
      threshold: CONFIG.thresholds.core,
      actual: Math.round(calcRate(severityStats.core) * 1000) / 1000,
      passed: calcRate(severityStats.core) >= CONFIG.thresholds.core,
    },
    nice_to_have: {
      threshold: CONFIG.thresholds.niceToHave,
      actual: Math.round(calcRate(severityStats.nice_to_have) * 1000) / 1000,
      passed: calcRate(severityStats.nice_to_have) >= CONFIG.thresholds.niceToHave,
    },
  };

  return {
    run_date: new Date().toISOString(),
    judge_model: CONFIG.judgeModel,
    total_scenarios: results.length,
    total_runs: results.reduce((a, r) => a + r.total_runs, 0),
    summary,
    latency_overall: computeLatency(overallLatencies),
    failures,
    critical_checks: criticalChecks,
    threshold_results: thresholdResults,
  };
}

// ── Save JSON ──────────────────────────────────────────────

export function saveReport(report: EvalReport): string {
  const dir = path.resolve(__dirname, "results");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const filePath = path.join(dir, `eval_${ts}.json`);
  fs.writeFileSync(filePath, JSON.stringify(report, null, 2), "utf-8");
  return filePath;
}

// ── Save full transcript ───────────────────────────────────

export function saveTranscript(results: ScenarioResult[], jsonPath?: string): string {
  const dir = path.resolve(__dirname, "results");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const ts = jsonPath
    ? path.basename(jsonPath, ".json").replace(/^eval_/, "")
    : new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const filePath = path.join(dir, `transcript_${ts}.json`);
  fs.writeFileSync(filePath, JSON.stringify(results, null, 2), "utf-8");
  return filePath;
}

// ── Save Markdown ──────────────────────────────────────────

export function saveMarkdownReport(report: EvalReport, jsonPath?: string): string {
  const dir = path.resolve(__dirname, "results");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const ts = jsonPath
    ? path.basename(jsonPath, ".json").replace(/^eval_/, "")
    : new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const filePath = path.join(dir, `eval_${ts}.md`);
  fs.writeFileSync(filePath, buildMarkdown(report), "utf-8");
  return filePath;
}

function buildMarkdown(report: EvalReport): string {
  const lines: string[] = [];
  const esc = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");

  lines.push(`# Eval Report — Psy Family Chatbot`);
  lines.push("");
  lines.push(`- **Date:** ${report.run_date}`);
  lines.push(`- **Judge:** ${report.judge_model}`);
  lines.push(`- **Scenarios:** ${report.total_scenarios}`);
  lines.push(`- **Total runs:** ${report.total_runs}`);
  lines.push("");

  // Thresholds
  lines.push(`## Thresholds`);
  lines.push("");
  lines.push(`| Severity | Actual | Required | Status |`);
  lines.push(`|---|---|---|---|`);
  for (const [level, t] of Object.entries(report.threshold_results)) {
    const pct = (t.actual * 100).toFixed(1);
    const req = (t.threshold * 100).toFixed(0);
    const mark = t.passed ? "✅ PASS" : "❌ FAIL";
    lines.push(`| ${level} | ${pct}% | ${req}% | ${mark} |`);
  }
  lines.push("");

  // Scenario summary
  lines.push(`## Scenario results`);
  lines.push("");
  lines.push(`| Scenario | Pass rate | Passed / Runs | Status | Avg ms | p95 ms | Max ms |`);
  lines.push(`|---|---|---|---|---|---|---|`);
  for (const [id, s] of Object.entries(report.summary)) {
    const pct = (s.pass_rate * 100).toFixed(1);
    const status =
      s.pass_rate >= 0.9 ? "✅ PASS" : s.pass_rate >= 0.7 ? "⚠️ WARN" : "❌ FAIL";
    const l = s.latency;
    lines.push(
      `| ${id} | ${pct}% | ${s.passed}/${s.runs} | ${status} | ${l.avg_ms} | ${l.p95_ms} | ${l.max_ms} |`,
    );
  }
  lines.push("");

  // Latency overall
  const lo = report.latency_overall;
  lines.push(`## Latency (bot /chat requests)`);
  lines.push("");
  lines.push(`- **Samples:** ${lo.count}`);
  lines.push(`- **Min:** ${lo.min_ms} ms`);
  lines.push(`- **Avg:** ${lo.avg_ms} ms`);
  lines.push(`- **p50:** ${lo.p50_ms} ms`);
  lines.push(`- **p95:** ${lo.p95_ms} ms`);
  lines.push(`- **Max:** ${lo.max_ms} ms`);
  lines.push("");

  // Critical checks
  if (Object.keys(report.critical_checks).length > 0) {
    lines.push(`## Critical checks`);
    lines.push("");
    lines.push(`| Check ID | Passed / Total | Pass rate | Status |`);
    lines.push(`|---|---|---|---|`);
    for (const [id, c] of Object.entries(report.critical_checks)) {
      const pct = (c.pass_rate * 100).toFixed(1);
      const mark = c.pass_rate >= 1.0 ? "✅" : "❌";
      lines.push(`| \`${id}\` | ${c.passed}/${c.total} | ${pct}% | ${mark} |`);
    }
    lines.push("");
  }

  // Failures
  lines.push(`## Failures (${report.failures.length})`);
  lines.push("");
  if (report.failures.length === 0) {
    lines.push(`_No failures._`);
  } else {
    for (const f of report.failures) {
      lines.push(`### [${f.scenario}] Run ${f.run}, Step ${f.step} — ${f.step_description}`);
      lines.push("");
      lines.push(`- **Check:** \`${f.check_id}\``);
      lines.push(`- **User:** ${esc(f.user_message)}`);
      lines.push("");
      lines.push(`**Bot response:**`);
      lines.push("");
      lines.push("```");
      lines.push(f.bot_response);
      lines.push("```");
      lines.push("");
      lines.push(`**Reason:** ${f.reason}`);
      lines.push("");
      lines.push("---");
      lines.push("");
    }
  }

  return lines.join("\n");
}

// ── Print human-readable report ────────────────────────────

export function printReport(report: EvalReport): void {
  const W = 80;
  const line = "═".repeat(W);
  const dash = "─".repeat(W);

  console.log(`\n${line}`);
  console.log(`  EVAL REPORT — Psy Family Chatbot`);
  console.log(`  Date:  ${report.run_date}`);
  console.log(`  Judge: ${report.judge_model}`);
  console.log(`  Runs:  ${report.total_runs} across ${report.total_scenarios} scenarios`);
  console.log(line);

  // Summary table
  console.log(`\n  SCENARIO RESULTS`);
  console.log(dash);
  for (const [id, s] of Object.entries(report.summary)) {
    const bar = s.pass_rate >= 0.9 ? "PASS" : s.pass_rate >= 0.7 ? "WARN" : "FAIL";
    const pct = (s.pass_rate * 100).toFixed(1);
    const lat = `avg=${s.latency.avg_ms}ms p95=${s.latency.p95_ms}ms`;
    console.log(
      `  ${id.padEnd(6)} ${pct.padStart(6)}%  (${s.passed}/${s.runs} runs)  [${bar}]  ${lat}`,
    );
  }

  // Latency overall
  const lo = report.latency_overall;
  console.log(`\n  LATENCY (bot /chat)`);
  console.log(dash);
  console.log(
    `  samples=${lo.count}  min=${lo.min_ms}ms  avg=${lo.avg_ms}ms  p50=${lo.p50_ms}ms  p95=${lo.p95_ms}ms  max=${lo.max_ms}ms`,
  );

  // Threshold results
  console.log(`\n  THRESHOLD CHECK`);
  console.log(dash);
  for (const [level, t] of Object.entries(report.threshold_results)) {
    const pct = (t.actual * 100).toFixed(1);
    const req = (t.threshold * 100).toFixed(0);
    const mark = t.passed ? "OK" : "FAIL";
    console.log(`  ${level.padEnd(14)} ${pct.padStart(6)}%  (need ${req}%)  [${mark}]`);
  }

  // Critical checks
  if (Object.keys(report.critical_checks).length > 0) {
    console.log(`\n  CRITICAL CHECKS`);
    console.log(dash);
    for (const [id, c] of Object.entries(report.critical_checks)) {
      const pct = (c.pass_rate * 100).toFixed(1);
      const mark = c.pass_rate >= 1.0 ? "OK" : "FAIL";
      console.log(`  ${id.padEnd(30)} ${c.passed}/${c.total}  (${pct}%)  [${mark}]`);
    }
  }

  // Failures (first 20)
  const fails = report.failures;
  if (fails.length > 0) {
    console.log(`\n  FAILURES (${fails.length} total, showing first 20)`);
    console.log(dash);
    for (const f of fails.slice(0, 20)) {
      console.log(`  [${f.scenario}] Run ${f.run}, Step ${f.step}: ${f.step_description}`);
      console.log(`    Check: ${f.check_id}`);
      console.log(`    User:  ${f.user_message}`);
      console.log(`    Bot:   ${f.bot_response.slice(0, 150)}...`);
      console.log(`    Reason: ${f.reason}`);
      console.log();
    }
  } else {
    console.log(`\n  No failures!`);
  }

  console.log(line);
}
