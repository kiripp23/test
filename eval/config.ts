// ── Eval configuration ──────────────────────────────────────

export const CONFIG = {
  // Bot HTTP API (FastAPI ai-agent-api)
  botEndpoint: process.env.BOT_ENDPOINT || "http://localhost:8000",

  // LLM judge (GPT via SOCKS5 proxy by default, override with env vars)
  judgeApiKey: process.env.JUDGE_API_KEY || process.env.LLM_API_KEY || "",
  judgeBaseUrl: process.env.JUDGE_BASE_URL || "https://api.openai.com/v1",
  judgeModel: process.env.JUDGE_MODEL || "gpt-4o",
  judgeProxy: process.env.JUDGE_PROXY || "", // socks5://user:pass@host:port

  // Default repeat count per scenario
  defaultRepeat: parseInt(process.env.EVAL_REPEAT || "10", 10),

  // Pass rate thresholds
  thresholds: {
    critical: 1.0, // 100% — encoding bugs, model leaks, booking without data
    core: 0.9,     // 90%  — booking flow, data collection, context
    niceToHave: 0.7, // 70% — empathy, tone, alternatives
  },

  // Request timeout (ms)
  requestTimeout: 60_000,

  // Delay between steps (ms) — avoid rate limiting
  stepDelay: 500,
};

// Load .env from project root
import * as fs from "fs";
import * as path from "path";

const envPath = path.resolve(__dirname, "../.env");
if (fs.existsSync(envPath)) {
  const lines = fs.readFileSync(envPath, "utf-8").split("\n");
  for (const line of lines) {
    const match = line.match(/^(\w+)=(.*)$/);
    if (match && !process.env[match[1]]) {
      process.env[match[1]] = match[2].trim();
      if (match[1] === "JUDGE_API_KEY") {
        CONFIG.judgeApiKey = match[2].trim();
      } else if (match[1] === "JUDGE_BASE_URL") {
        CONFIG.judgeBaseUrl = match[2].trim();
      } else if (match[1] === "JUDGE_MODEL") {
        CONFIG.judgeModel = match[2].trim();
      } else if (match[1] === "JUDGE_PROXY") {
        CONFIG.judgeProxy = match[2].trim();
      } else if (match[1] === "LLM_API_KEY" && !CONFIG.judgeApiKey) {
        CONFIG.judgeApiKey = match[2].trim();
      }
    }
  }
}
