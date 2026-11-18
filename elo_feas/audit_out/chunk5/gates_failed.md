# Chunk 5: gates failed

**diagnostic, no re-selection.** Failed gates: G3. No loss was computed; compare.py was not imported or run; gates_passed.json was not written.

## G3
(event, book) pairs: 155.
- both prices valid American odds (|price| >= 100): 155 of 155
- implied probabilities in (0,1): 155 of 155; implied sum > 1: 155 of 155
- proportional probabilities sum to 1 within 1e-12: 155 of 155; both in (0,1): 155 of 155
- recomputed proportional probabilities equal the stored team-keyed probabilities within 1e-9: **6 of 155** (max |diff| team_a side 4.95e-07, team_b side 4.95e-07).
- the stored probabilities are written with at most 6 decimals (decimal places: {1: 2, 2: 8, 3: 2, 4: 6, 5: 20, 6: 272}); within 5e-7 (half a unit in the 6th decimal): 155 of 155. This is stated as a fact about the file; the gate tolerance is not changed.

