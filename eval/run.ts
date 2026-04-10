// ── Entry point for eval runner ─────────────────────────────

import { CONFIG } from "./config";
import { ALL_SCENARIOS, getScenarioById } from "./scenarios";
import { runScenario, ScenarioResult } from "./runner";
import { buildReport, saveReport, saveMarkdownReport, saveTranscript, printReport } from "./report";

function parseArgs(): {
  scenario?: string;
  repeat?: number;
  criticalOnly: boolean;
} {
  const args = process.argv.slice(2);
  let scenario: string | undefined;
  let repeat: number | undefined;
  let criticalOnly = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--scenario" && args[i + 1]) {
      scenario = args[++i];
    } else if (args[i] === "--repeat" && args[i + 1]) {
      repeat = parseInt(args[++i], 10);
    } else if (args[i] === "--critical-only") {
      criticalOnly = true;
    }
  }

  return { scenario, repeat, criticalOnly };
}

async function main() {
  const { scenario, repeat, criticalOnly } = parseArgs();

  if (!CONFIG.judgeApiKey) {
    console.error("ERROR: LLM_API_KEY env var is required (used for judge)");
    process.exit(1);
  }

  console.log("╔══════════════════════════════════════════════════════════════╗");
  console.log("║  Psy Family Eval Runner                                     ║");
  console.log(`║  Judge model: ${CONFIG.judgeModel.padEnd(45)}║`);
  console.log(`║  Bot endpoint: ${CONFIG.botEndpoint.padEnd(44)}║`);
  console.log(`║  Repeat: ${(repeat ?? CONFIG.defaultRepeat).toString().padEnd(50)}║`);
  console.log(`║  Critical only: ${criticalOnly.toString().padEnd(43)}║`);
  console.log("╚══════════════════════════════════════════════════════════════╝\n");

  // Select scenarios
  let scenarios = ALL_SCENARIOS;
  if (scenario) {
    const found = getScenarioById(scenario);
    if (!found) {
      console.error(`Scenario not found: ${scenario}`);
      console.error(`Available: ${ALL_SCENARIOS.map((s) => s.id).join(", ")}`);
      process.exit(1);
    }
    scenarios = [found];
  }

  // Run
  const results: ScenarioResult[] = [];

  for (const sc of scenarios) {
    console.log(`\n▸ ${sc.id}: ${sc.name}`);
    console.log(`  ${sc.description}`);
    const result = await runScenario(sc, repeat, criticalOnly);
    results.push(result);
    const pct = (result.pass_rate * 100).toFixed(1);
    console.log(`  → ${pct}% pass rate (${result.passed_runs}/${result.total_runs})\n`);
  }

  // Report
  const report = buildReport(results);
  const filePath = saveReport(report);
  const mdPath = saveMarkdownReport(report, filePath);
  const transcriptPath = saveTranscript(results, filePath);
  printReport(report);

  console.log(`\nJSON report saved to: ${filePath}`);
  console.log(`Markdown report saved to: ${mdPath}`);
  console.log(`Full transcript saved to: ${transcriptPath}`);

  // Exit code based on thresholds
  const critFail = !report.threshold_results.critical.passed;
  const coreFail = !report.threshold_results.core.passed;

  if (critFail) {
    console.error("\n!!! CRITICAL threshold not met — immediate action required !!!");
    process.exit(2);
  }
  if (coreFail) {
    console.error("\n! Core threshold not met — needs prompt improvement");
    process.exit(1);
  }

  console.log("\nAll thresholds met.");
  process.exit(0);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
