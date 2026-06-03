# autorep -w fixtures

Synthetic samples of `autorep -j {name} -w ...` stdout, modelled on the
documented Broadcom column layout for AutoSys 12.x and 24.x. Replace each
with captured production output (anonymised) when a real customer install is
available — see development plan M7 sub-task 7 and the risk-register row on
"`autorep -w` text-parsing for history".

The 24.x fixture adds a trailing `Machine` column that the 12.x output omits;
both are filtered through the same parser at
[backend/adapters/autorep_parser.py](../../adapters/autorep_parser.py).
