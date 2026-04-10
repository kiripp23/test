// ── LLM Judge — evaluates bot responses via OpenAI API (with SOCKS5 proxy support)

import * as https from "https";
import * as http from "http";
import { URL } from "url";
import { CONFIG } from "./config";

// Dynamic import for socks-proxy-agent (only when proxy is configured)
let SocksProxyAgent: any = null;

export interface JudgeVerdict {
  pass: boolean;
  reason: string;
}

interface Message {
  role: string;
  content: string;
}

const JUDGE_SYSTEM_PROMPT = `Ты — строгий QA-инженер, оценивающий ответы чат-бота клиники Psy Family.

Правила оценки:
- Будь строгим. Если критерий требует ответа на ОБА вопроса, а бот ответил только на один — это FAIL.
- Если бот проигнорировал вопрос пользователя — это FAIL.
- Если бот сбросил контекст (заново поздоровался без причины) — это FAIL.
- Пустой ответ — всегда FAIL.
- Ответ не по теме вопроса — FAIL.
- Если бот завершил запись без сбора имени и даты рождения — это CRITICAL FAIL.
- Если бот раскрыл, что он ИИ/модель/бот — это FAIL.

Ответь строго в формате JSON (без markdown-обёртки):
{"pass": true, "reason": "краткое объяснение на русском"}
или
{"pass": false, "reason": "краткое объяснение на русском"}`;

function buildJudgePrompt(
  history: { role: string; content: string }[],
  userMessage: string,
  botResponse: string,
  criteria: string,
): string {
  let ctx = "";
  if (history.length > 0) {
    ctx = "Предыдущий контекст диалога:\n";
    for (const msg of history) {
      const label = msg.role === "user" ? "Пользователь" : "Бот";
      ctx += `${label}: ${msg.content}\n`;
    }
    ctx += "\n";
  }

  return `${ctx}Последнее сообщение пользователя: ${userMessage}
Ответ бота: ${botResponse}

Критерий проверки: ${criteria}`;
}

async function getProxyAgent(): Promise<any> {
  if (!CONFIG.judgeProxy) return undefined;

  if (!SocksProxyAgent) {
    const mod = await import("socks-proxy-agent");
    SocksProxyAgent = mod.SocksProxyAgent;
  }

  return new SocksProxyAgent(CONFIG.judgeProxy);
}

function httpRequest(
  url: string,
  headers: Record<string, string>,
  body: string,
  agent?: any,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const mod = parsedUrl.protocol === "https:" ? https : http;

    const options: https.RequestOptions = {
      method: "POST",
      headers: {
        ...headers,
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body).toString(),
      },
      agent: agent,
    };

    const req = mod.request(parsedUrl, options, (res) => {
      let data = "";
      res.on("data", (chunk: Buffer) => (data += chunk.toString()));
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 500)}`));
        } else {
          resolve(data);
        }
      });
    });

    req.on("error", reject);
    req.setTimeout(CONFIG.requestTimeout, () => {
      req.destroy(new Error("Request timeout"));
    });
    req.write(body);
    req.end();
  });
}

export async function judgeResponse(
  history: { role: string; content: string }[],
  userMessage: string,
  botResponse: string,
  criteria: string,
): Promise<JudgeVerdict> {
  const messages: Message[] = [
    { role: "system", content: JUDGE_SYSTEM_PROMPT },
    {
      role: "user",
      content: buildJudgePrompt(history, userMessage, botResponse, criteria),
    },
  ];

  // GPT-5+ uses max_completion_tokens, older models use max_tokens
  const isNewGpt = CONFIG.judgeModel.startsWith("gpt-5") || CONFIG.judgeModel.startsWith("o");
  const tokenParam = isNewGpt ? "max_completion_tokens" : "max_tokens";

  const payload = JSON.stringify({
    model: CONFIG.judgeModel,
    messages,
    temperature: 0,
    [tokenParam]: 300,
  });

  try {
    const url = `${CONFIG.judgeBaseUrl}/chat/completions`;
    const agent = await getProxyAgent();

    const raw = await httpRequest(
      url,
      { Authorization: `Bearer ${CONFIG.judgeApiKey}` },
      payload,
      agent,
    );

    const resp = JSON.parse(raw);
    const text: string = resp.choices?.[0]?.message?.content || "";

    // Extract JSON from response (handle markdown wrapping)
    const jsonMatch = text.match(/\{[\s\S]*?\}/);
    if (!jsonMatch) {
      return { pass: false, reason: `Judge returned non-JSON: ${text.slice(0, 200)}` };
    }

    const verdict = JSON.parse(jsonMatch[0]) as JudgeVerdict;
    return verdict;
  } catch (err: any) {
    return { pass: false, reason: `Judge error: ${err.message}` };
  }
}
