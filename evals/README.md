# Evals

## Meal estimation (`evals/meal_estimation`)

A fixed set of meals with reference energy (`cases.json`, CREA / USDA values)
run through the production text pipeline: prompt, provider, validation and the
stated-quantity lock. No database is used and no user data is read.

```bash
railway run -s "Calory Backend" -- python -m evals.meal_estimation.run
railway run -s "Calory Backend" -- python -m evals.meal_estimation.run --compare evals/results/<earlier>.json
```

Run it before every prompt or model change and compare with the last result
in `evals/results/`. Metrics:

| Metric | Meaning |
|---|---|
| `within_tolerance_pct` | cases whose estimate is inside the case's tolerance |
| `mean_abs_error_pct`, `median_abs_error_pct` | distance from the reference |
| `bias_pct` | mean signed error: positive means overestimating |
| `stated_honored_pct` | amounts the person wrote that the estimate kept (±5%) |
| `category_ok_pct` | explicit wording ("a colazione") read as a high-confidence category |
| `latency_p50_ms`, `latency_p90_ms` | one non-streaming call |

Options: `--no-lock` (measure the lock's effect), `--repeat N` (stability:
keeps the median run and reports the spread), `--only id1,id2`, `--label`.

Adding a case: give it a reference you can source and a tolerance that
matches how precise the text is (about 10-15% for a weighed single food,
25-40% for a dish with an unknown recipe). `tests/test_meal_eval.py` checks
the file stays well formed.
