# Working agreement

60-minute timed build session, screen-recorded. I narrate my decisions aloud; you are my pair programmer. Optimise for a working, pushed result and clear reasoning, not volume of code.

## Workflow
- TASK.md holds the spec. Re-read it before planning and before calling anything done.
- Before the first build step and before any multi-file change, give a plan (5 bullets max) and wait for my go-ahead.
- Small slices: one feature per change, about 100 changed lines max. Stop after each slice so I can run and review it.
- After each change, say in 1-2 lines what changed and the exact command to run or test it.
- State assumptions and risks explicitly. Choose the simplest approach that meets the spec. No speculative features, abstractions or new dependencies without asking.
- If the same fix fails twice, stop and propose a simpler alternative instead of looping.

## Secrets
- Never read, print, cat, grep, echo or log .env or any key value, including in command output or error messages.
- Load keys only through python-dotenv / os.environ by variable name. Never hardcode a key.
- Run `git status` before every commit and confirm .env is not staged.

## Stack defaults
- Always use the project .venv interpreter and pip, never the system Python.
- Preinstalled: anthropic, openai, python-dotenv, requests, httpx, websockets, pandas, fastapi, uvicorn, streamlit, pytest. Prefer these over new dependencies.
- UI only if the spec needs one: Streamlit for a fast UI over Python logic; React + Vite only if a real frontend is required.
- LLM calls: key from env, explicit timeout, clear error message on API failure.

## Git
- After every working slice: commit with a short imperative message, then push.
- Never force-push or rewrite history.

## When I say "wrap up"
- Update README.md: what it does, how to run, approach and tradeoffs, how AI was used, limitations and next steps.
- Write requirements.txt with only the packages the code imports.
- Make sure .env.example lists every env var the code reads.
- Show `git status`, then commit and push.
