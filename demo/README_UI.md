# Privacy Gateway — Live Inspector (local demo UI)

**Synthetic data only.** The inspector shows original values next to their aliases. It binds to `127.0.0.1` only; never expose it.

## Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/runtime.txt
pip install uvicorn==0.54.0
# optional, recommended (local NER safety net):
pip install --require-hashes -r requirements/ner.txt
pip install -r requirements/ner-model.txt

cp .env.example .env && chmod 600 .env   # then put your key in .env: GROQ_API_KEY=gsk_...
                                   # (.env is git-ignored; an exported var or Codespaces secret wins)
                                   # without a key a clearly-labelled mock LLM is used
python -m demo.ui_server           # open http://127.0.0.1:8765
```

## What you see

The page is built for two audiences at once: HR people who want to see *what happens to their data*, and experts who want the evidence.

- **The journey** (top): Your message → Privacy shield → Safety check → AI → Back to you. A dashed line marks the company boundary; only the AI sits outside it. Glowing particles flow along the path: coloured (personal data) before the shield, uniform blue (placeholders) after it.
- **Three views, side by side:**
  - *What you typed*: each detected or declared value lights up in its colour;
  - *What the AI saw*: the exact text that left, with placeholder chips (`[PERSON_…]`, withheld `[BANK_ACCOUNT]`), then the AI's raw reply;
  - *What you got back*: the answer after the gateway put the real values back.

  Each value visibly lifts off and morphs into its placeholder on the way out, and back into the real value on the way in.
- **Colours are the same everywhere:** violet = people, blue = ID and account numbers, gold = money, pink = dates, teal = contact details. Striped grey = withheld (never sent, never put back).
- **Step by step:** five plain-language steps ("We hide personal details", "Safety check before anything leaves", …).
- **What we checked on THIS request:** the server's own checks, with a plain-language title, the server's exact wording (for example "… cannot show that undetected personal data did not leak") and its status. N/A checks are listed separately. The page never claims more than these checks.
- **Outcomes:**
  - green "Personal details protected for this request" only when every applicable check passed;
  - amber "Kept inside the company" for the private route, which says that the answer comes from a demo stand-in, not a real AI;
  - red "Stopped on purpose" when the gateway fails closed.
- **The run is replayed at a human pace.** A real run takes milliseconds. The header shows "Slowed down so you can follow it. Real run: N ms".
- **Show technical details** (toggle): the status bar (provider, NER, integrity digest, commit, private-route backend, warnings), the server's 13+ step names with timings and details, the body size and SHA-256, raw payloads (app input, provider request pretty-printed with its caption, raw response, restored answer) and the placeholder map.
- **How it works:** a four-step illustrated story for first-time viewers.
- **Mode:** the inspector runs the gateway in `development` mode (the trace hook is refused in production). It plays the source system and signs each request with the contract's committed DEMO source key (`X-PGW-Input-Source`/`-Timestamp`/`-Nonce`/`-Signature`, bound to the app id, `pgw.product.attest`). Production refuses the demo keys. The integrity digest is the shipped policy and contracts checked against the unsigned repo manifest, which is valid for development only.
- **Scenarios** (cards on the left; "Edit the message yourself" opens the editor for free text, purpose, declared subjects JSON and conversation id):
  - A1–A4: external LLM on protected text, with declared subjects.
  - B1–B3: local computation. Exact salaries and wages never leave.
  - C4: a Hindi ticket on the private, in-boundary route.
  - MT: multi-turn. Run it, then press **Continue conversation**.
  - FAIL: a fail-closed refusal.
- **Checks semantics:** the "values absent" check covers only values the gateway **detected or the app declared in this request**. On the private route the external-egress, declared-subject and alias checks are N/A, and a separate check verifies that the stand-in's answer withholds the C3 values and long digit runs found in the input.

The private route uses an extractive stand-in, not an LLM, until a local GGUF model is configured (`PGW_PRIVATE_GGUF`).

Accessibility: everything is reachable by keyboard (cards and toggles are buttons; Esc closes dialogs and presenter mode). With `prefers-reduced-motion`, the background and particles are still, nothing flies, and the story plays about three times faster. The layout works down to phone width (390 px).

## Presenter mode (for a screen recording)

Press **Play the demo**, or open `http://127.0.0.1:8765/?present=1` to start it automatically. It switches to a 16:9 framing with no scrolling, hides the editor, and plays a captioned sequence of four real runs:

1. A payroll ticket goes to an AI (A1);
2. Who earns more, without sending salaries (B1);
3. A ticket the gateway can't read reliably stays inside (C4);
4. Something required is missing, so the gateway stops (FAIL).

It takes about a minute. Esc or **Exit** stops it.

### Recording tips for LinkedIn

- Record a 1920×1080 browser window at 100 % zoom (full-screen the browser with F11 so only the page is visible). LinkedIn accepts 16:9 MP4 up to 10 minutes; under 60 seconds works best in the feed.
- Without a key the mock LLM is used and the page says so ("Mock AI · templated answers"). For a recording with real AI wording, set `GROQ_API_KEY`. The data is still synthetic, and the provider's retention terms are not attested for a demo key.
- Leave the "Demo · synthetic data only" badge in frame. Don't crop out the check wording.
- Headless capture with Playwright: open a 1920×1080 context with `record_video_dir`, load `/?present=1`, and wait until `body.presenting` is gone. Then convert the WebM: `ffmpeg -i in.webm -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart out.mp4`.
- Add captions in the post itself. The page's own captions are burned in, so the video also works muted.

## Local-request protections

The UI can spend the configured provider key, so it accepts only local, same-page requests:

- **Network binding:** it binds `127.0.0.1` only.
- **Host header:** it must be `127.0.0.1:<port>` or `localhost:<port>`; anything else gets 421. This blocks DNS-rebinding pages.
- **`POST /api/run` requirements:**
  - the exact same-origin `Origin` header;
  - an `X-PGW-UI: 1` header;
  - `Content-Type: application/json`.

  Any other origin triggers a CORS preflight, which the server refuses without CORS headers. Plain HTML forms cannot set the header or the JSON type.
- **Size and concurrency limits:** request bodies up to 64 KB and 20 messages; one run at a time (429 if busy). The gateway's own size, time and concurrency limits apply after that.
- **Response headers:** a strict CSP (`frame-ancestors 'none'`, `connect-src 'self'`), `X-Frame-Options: DENY`, `nosniff` and `no-referrer`.
- **The page:** a single static file with inline CSS, SVG and JS and a system font stack. It has no CDN, web font or external image, so it works under the same-origin CSP. Untrusted text (tickets, model output, payloads) is inserted only with `textContent` and `createElement`, never parsed as HTML. `tests/test_ui_server.py` scans the page source for this.

None of this makes the inspector safe to expose on a network. It shows original values next to aliases by design.

## GitHub Codespaces

- Run the same commands in the Codespace terminal and open the forwarded port 8765.
- The server detects the Codespace (`CODESPACE_NAME`, `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN`) and allows **exactly** that forwarded origin (`https://<codespace>-8765.app.github.dev`). Nothing broader is allowed.
- Keep the port's visibility **Private**: forwarded Public ports are reachable by anyone with the URL.
- For any other port-forwarding URL, pass it explicitly: `--allow-origin https://exact.host`.

## Troubleshooting a 502 "upstream provider error"

The gateway fails closed, so the app gets no content. The timeline's **LLM responded** step, and the status bar, show the cause and a hint:

| Cause | Meaning |
|---|---|
| `upstream status 401/403` | The key was rejected. In the terminal that starts the server, run `echo ${#GROQ_API_KEY}` to check the key is set; a Groq key is about 56 characters. Make sure it is current (not rotated), with no quotes or spaces. |
| `upstream status 429` | Rate limit |
| `upstream status 400/404` | The model or parameters were not accepted for this key |
| `transport error` | The provider cannot be reached from this machine |

At startup the server checks the key once and prints `Groq key check: verified` or the reason it failed.
