You are reviewing one flag from a forecasting study. Judge only the text below. Do not read any file, search the web, or run any command.

Each forecaster got a brief with this instruction, word for word the same in every version of the brief:

> 10. Before you research, record in `forecaster.prior_context` anything in your context other than this
>     brief (system prompt, memory, instructions, earlier conversation) that mentions Alaska's 2026
>     elections or forecasts of these questions. Quote it, or write "none".

The registered rule: a run whose `prior_context` mentions these questions stays out of the analysis unless the flag is accepted. An automatic check flags every `prior_context` that contains a word from a fixed list (this one matched "Alaska"), unless the field reads only "none". Clear the flag only if the text shows that the forecaster had nothing in its context, beyond the brief, that mentions Alaska's 2026 elections or forecasts of these questions.

The flagged `prior_context`, verbatim:

> none (the system context included only the user's email address and harness/plan-mode instructions; nothing about Alaska's 2026 elections or these questions)

Reply with:
1. CLEAR or KEEP OUT, alone on the first line.
2. Two or three sentences on why.
3. Anything in the text that leaves you unsure, or "nothing".
