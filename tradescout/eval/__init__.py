"""Out-of-sample evaluation harness. Everything here is walk-forward: a forecast for a match day
only ever uses matches before that day, and parameter choices made on the tuning seasons are
confirmed on a held-out season that was never used for tuning."""
