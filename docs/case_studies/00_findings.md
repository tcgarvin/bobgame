# Jev versus chat models: headline findings

Written 2026-09-19 from matrix run `20260919T053353Z-b6f6f7`: 44 scenarios,
3 repeats, 16 backends. Each numbered file in this folder is one scenario.
`README.md` is the generated index. Latency is not compared here; the
network it was measured on is not trusted. Jev's price is $42 per billion
input tokens; OpenRouter prices are the per-request cost OpenRouter reports.

## The three questions

1. Can a model at Jev's price choose the same actions? Nearly, for one model.
2. Can it make Jev's four judgements (done, stuck, lost, danger)? Not by
   writing the numbers down. Yes, for one model, when the numbers are read off
   its token logprobs instead, at about twice Jev's cost.
3. Can a model at 10x Jev's price do both? On actions yes. On judgements, only
   danger and done, and only as a yes or no.

## Summary

Pass rate is the assertion pass rate over 132 tries. AUC is the pairwise
ordering accuracy of positive against negative scenarios for that judgement
(0.5 is chance, 1.0 is perfect ordering). Cost is per 100 ticks of one settler.

| backend | pass rate | AUC done | AUC stuck | AUC lost | AUC danger | consistency | $/100 calls | x Jev |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| jev-latest | 89% | 1.00 | 0.67 | 1.00 | 1.00 | 95% | 0.0064 | 1 |
| gemini-2.5-flash | 90% | 0.83 | 0.75 | 0.50 | 1.00 | 98% | 0.0714 | 11 |
| gpt-4.1-mini | 89% | 1.00 | 0.50 | 0.50 | 1.00 | 98% | 0.0540 | 8 |
| gpt-oss-120b @ Cerebras | 89% | 1.00 | 0.75 | 0.75 | 1.00 | 98% | 0.0752 | 12 |
| qwen3.7-flash, logprob | 88% | 1.00 | 0.92 | 1.00 | 1.00 | 95% | 0.0143 | 2.2 |
| gpt-oss-20b @ Groq | 87% | 0.82 | 0.50 | 0.71 | 1.00 | 89% | 0.0154 | 2.4 |
| qwen3.7-flash | 86% | 0.72 | 0.50 | 0.50 | 1.00 | 98% | 0.0051 | 0.8 |
| gemini-2.5-flash-lite | 80% | 0.67 | 0.50 | 0.50 | 0.83 | 100% | 0.0186 | 2.9 |
| qwen3.7-flash, vote | 78% | 0.74 | 0.75 | 0.75 | 1.00 | 89% | 0.0168 | 2.6 |
| gpt-4.1-nano, logprob | 72% | 0.64 | 0.29 | 0.83 | 0.92 | 98% | 0.0528 | 8 |
| mistral-nemo @ DeepInfra | 71% | 0.50 | 0.50 | 0.50 | 1.00 | 95% | 0.0029 | 0.5 |
| llama-3.1-8b @ Groq | 70% | 0.50 | 0.50 | 0.50 | 0.97 | 100% | 0.0074 | 1.2 |
| gpt-4.1-nano | 70% | 0.50 | 0.50 | 0.50 | 1.00 | 98% | 0.0130 | 2.0 |
| gpt-5-nano (minimal reasoning) | 68% | 0.56 | 0.92 | 0.42 | 0.90 | 89% | 0.0119 | 1.9 |
| gpt-4.1-nano, vote | 67% | 0.33 | 0.50 | 0.50 | 1.00 | 89% | 0.0421 | 6.6 |
| gpt-5-nano, vote | 65% | 0.37 | 0.50 | 0.50 | 1.00 | 93% | 0.0333 | 5.2 |

Not run: gemini-2.5-flash-lite with vote elicitation (a config error, fixed
since). Not possible: logprobs on Gemini, on Groq, and on every OpenAI
reasoning model, all of which refuse or ignore the request.

## Findings

**Actions.** At Jev's price only qwen3.7-flash comes close on which action to
take, three points behind Jev over 132 tries. The 8B to 12B open models and
gpt-5-nano are 20 points behind, and their misses are the interesting ones:
attacking the fellow settler standing beside them (23), mining a rock while
starving at 6 health (32), chopping wood instead of walking to a cry for help
(37). At 8x to 12x Jev's price, gemini-2.5-flash, gpt-4.1-mini and
gpt-oss-120b match Jev's action pass rate exactly.

**Judgements, self-reported.** Every chat model orders danger correctly; a
wolf next to a wounded settler is not subtle. On done, lost and stuck the
cheap models are at chance, and the decile tables show why. Asked to write a
probability, qwen3.7-flash writes only 0.0 or 1.0; gpt-4.1-mini writes 0.0 for
stuck 132 times out of 132; gpt-5-nano puts 124 of 131 done values under 0.1,
including on the plainly finished jobs. No cutoff separates their positives
from their negatives, so a stint driven by these numbers would either never
end or end at random. Jev's values spread across the range and separate
cleanly on done, lost and danger with a margin. Jev's one weak judgement is
stuck, where its positives and negatives overlap (best balanced accuracy 75%).
That matches the two scenarios it fails outright, 19 and 31, both of which ask
it to notice a missing station or tool tier.

**Judgements, read off logprobs.** Asking the same qwen3.7-flash the four
yes/no questions one token at a time and reading P(yes) from the logprobs
changes the picture entirely: AUC 1.00 on done, lost and danger and 0.92 on
stuck, which is better ordering than Jev on stuck. Two caveats. The values are
tiny: the best cutoffs are 0.10 for done and 0.05 for lost, with margins of
0.03 and 0.05, so the stint's 0.6 thresholds would have to be retuned per
model and the ordering is correct but fragile. And it takes five calls per
tick instead of one, so the cost is 2.2x Jev's and the errors go up (three
429s from Alibaba in 132 ticks). Only two of the tested endpoints return
logprobs at all.

**Judgements, by voting.** Sampling five answers at temperature 1 and taking
the yes fraction did not help and hurt the action accuracy (qwen fell from 86%
to 78%; gpt-4.1-nano and gpt-5-nano stayed in the 60s). Five votes can only
produce multiples of 0.2, and on done the models mostly voted unanimously the
wrong way.

**Consistency.** Every backend picks the same top action across the three
repeats on 89% to 100% of scenarios. Jev is 95%; its one flip (41) is between
two equally correct options. Its other misses are marginal: on scenario 22 it
takes the right step every time but reads lost at 0.43, 0.46 and 0.48 against
a 0.45 test cutoff, well under the 0.6 that would end a stint.

**What this means for the project.** A drop-in replacement for Jev at Jev's
price does not exist among the models tested. The nearest thing is
qwen3.7-flash with logprob elicitation at about twice the cost and five HTTP
calls per tick, with the stint thresholds retuned; it would then match Jev on
actions and on ordering the judgements. The 10x models match Jev on actions
but not on lost or stuck, so the planner would get its turn back late or
never. Latency remains untested and is the next question.
