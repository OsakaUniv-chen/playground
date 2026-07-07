#!/usr/bin/env Rscript
# Does believing the robot is human-teleoperated predict a better GQS/GEQ score?
# Reads the long CSV from belief_vs_eval.py and, per subscale on the 4 robot
# conditions, fits two individual-level linear mixed models:
#   (A) y ~ belief_yes + (1|group) + (1|participant)   belief_yes in {0,1}
#   (B) y ~ PTL01      + (1|group) + (1|participant)   PTL rescaled to [0,1]
# Reports the belief/PTL slope (= Yes-No, resp. No->fully-Yes over PTL range),
# 95% CI, p, and a standardized effect d = estimate / total SD (Westfall-style).
# Singular fits drop (1|group) and refit with (1|participant) only.

suppressPackageStartupMessages({
  library(lme4); library(lmerTest)
})

args <- commandArgs(trailingOnly = TRUE)
csv  <- if (length(args) >= 1) args[1] else "belief_long.csv"
d0   <- read.csv(csv, stringsAsFactors = FALSE, na.strings = c("", "NA"))
d0   <- d0[d0$mode %in% c("Tele", "PSSP", "DoA", "Random"), ]
d0$PTL01 <- d0$PTL / 100

SCALES <- c("Anthropomorphism", "Animacy", "Likeability",
            "PerceivedIntelligence", "PerceivedSafety",
            "Flow", "Competence", "PositiveAffect", "NegativeAffect", "Tension")
BETTER_LOW <- c("NegativeAffect", "Tension")

line <- function() cat(strrep("=", 78), "\n", sep = "")
cat("Model: y ~ <belief> + (1|group) + (1|participant); robot 4 conds only.\n")
cat("(A) belief_yes (0/1): est = mean(Yes) - mean(No).\n")
cat("(B) PTL01 (0..1): est = change over the full 0->100% PTL range.\n")
cat("d = est / total SD.  For NegativeAffect/Tension, higher = worse.\n")

fit_term <- function(name, term) {
  d <- d0[!is.na(d0[[name]]) & !is.na(d0[[term]]), ]
  if (nrow(d) < 8) { cat(sprintf("   %-8s insufficient data\n", term)); return() }
  d$group <- factor(d$group); d$participant <- factor(d$participant)
  f  <- as.formula(sprintf("%s ~ %s + (1|group) + (1|participant)", name, term))
  fit <- function(fm) tryCatch(suppressWarnings(lmer(fm, d, REML = TRUE)),
                               error = function(e) e)
  m <- fit(f); note <- ""
  if (!inherits(m, "error") && isSingular(m, tol = 1e-4)) {
    m2 <- fit(as.formula(sprintf("%s ~ %s + (1|participant)", name, term)))
    if (!inherits(m2, "error")) { m <- m2; note <- " [dropped (1|group)]" }
  }
  if (inherits(m, "error")) { cat("   model failed\n"); return() }
  co <- summary(m)$coefficients
  ci <- tryCatch(confint(m, parm = term, method = "Wald"),
                 error = function(e) matrix(NA, 1, 2))
  est <- co[term, "Estimate"]; p <- co[term, "Pr(>|t|)"]
  vc <- as.data.frame(VarCorr(m)); total_sd <- sqrt(sum(vc$vcov))
  cat(sprintf("   %-8s est=%+.3f [%+.3f, %+.3f]  d=%+.2f  p=%.3f%s\n",
              term, est, ci[1, 1], ci[1, 2], est / total_sd, p, note))
}

for (nm in SCALES) {
  if (is.null(d0[[nm]])) next
  cat(sprintf("\n* %s%s\n", nm, if (nm %in% BETTER_LOW) "  (higher = worse)" else ""))
  fit_term(nm, "belief_yes")
  fit_term(nm, "PTL01")
}
cat("\n")
