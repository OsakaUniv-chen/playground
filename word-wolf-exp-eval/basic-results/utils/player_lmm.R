#!/usr/bin/env Rscript
# Individual-level linear mixed models for the player (in-person) experiment.
# See player-eval.md §4. Reads the long CSV exported by post_survey.py and, for
# each subscale on the 4 robot conditions, fits
#     y ~ mode + (1 | group) + (1 | participant)
# tests the overall mode effect (F + Satterthwaite df), then runs the 3 planned
# PSSP contrasts (Holm-adjusted) with 95% CIs and a standardized effect size d.
# A direction is claimed only when a contrast's 95% CI excludes 0; GEQ is
# exploratory (no directional claim).

suppressPackageStartupMessages({
  library(lme4)
  library(lmerTest)
  library(emmeans)
})

args <- commandArgs(trailingOnly = TRUE)
csv  <- if (length(args) >= 1) args[1] else "post_survey_long.csv"
d0   <- read.csv(csv, stringsAsFactors = FALSE, na.strings = c("", "NA"))

ROBOT <- c("Tele", "PSSP", "DoA", "Random")
# subscale -> exploratory? (GEQ is exploratory; GQS and PTL are confirmatory)
SCALES <- list(
  Anthropomorphism = FALSE, Animacy = FALSE, Likeability = FALSE,
  PerceivedIntelligence = FALSE, PerceivedSafety = FALSE,
  Flow = TRUE, Competence = TRUE, PositiveAffect = TRUE,
  NegativeAffect = TRUE, Tension = TRUE,
  PTL = FALSE
)
PAIRS <- list(c("PSSP", "DoA"), c("PSSP", "Random"), c("PSSP", "Tele"))
emm_options(lmerTest.limit = 1e5)

line <- function() cat(strrep("=", 78), "\n", sep = "")
line()
cat("Individual-level linear mixed model (player-eval.md §4)\n")
line()
cat("Model: y ~ mode + (1|group) + (1|participant); robot 4 conditions only.\n")
cat("Omnibus: F + Satterthwaite df.  Contrasts: PSSP - X, Holm-adjusted, with\n")
cat("95% CI and standardized effect size d (= estimate / total SD, i.e.\n")
cat("sqrt(var_group + var_participant + var_residual); Westfall et al.).\n")
cat("dir: PSSP> / PSSP< = 95% CI excludes 0 (higher/lower); ns = includes 0;\n")
cat("     exp = exploratory (GEQ, no directional claim).\n")
cat("Singular fits (variance ~ 0) drop (1|group) and refit with (1|participant).\n")

fit_one <- function(name, exploratory) {
  cat(sprintf("\n* %s%s\n", name, if (exploratory) "  (EXPLORATORY)" else ""))
  if (is.null(d0[[name]])) { cat("   column not found\n"); return(invisible()) }
  d <- d0[d0$mode %in% ROBOT & !is.na(d0[[name]]), ]
  d$mode        <- droplevels(factor(d$mode, levels = ROBOT))
  d$group       <- factor(d$group)
  d$participant <- factor(d$participant)
  n  <- nrow(d); ng <- nlevels(d$group); lev <- levels(d$mode)
  if (n < 8 || length(lev) < 2) {
    cat(sprintf("   insufficient data (n=%d obs, %d groups, %d conditions)\n",
                n, ng, length(lev)))
    return(invisible())
  }
  full_f <- as.formula(
    sprintf("%s ~ mode + (1|group) + (1|participant)", name))
  fit <- function(formula) tryCatch(suppressWarnings(
           lmer(formula, data = d, REML = TRUE)), error = function(e) e)
  m <- fit(full_f); reduced <- FALSE
  # Singular fit (e.g. group variance ~ 0, common with few groups): drop the
  # group random intercept and refit with participant only (still handles the
  # repeated measures). Fixed-effect estimates stay valid either way.
  if (!inherits(m, "error") && isSingular(m, tol = 1e-4)) {
    m2 <- fit(as.formula(sprintf("%s ~ mode + (1|participant)", name)))
    if (!inherits(m2, "error")) { m <- m2; reduced <- TRUE }
  }
  if (inherits(m, "error")) {
    cat("   model failed:", conditionMessage(m), "\n"); return(invisible())
  }
  note <- if (reduced) "  [reduced: dropped (1|group), singular]" else ""

  av <- tryCatch(anova(m, ddf = "Satterthwaite"), error = function(e) NULL)
  if (!is.null(av) && nrow(av) >= 1) {
    cat(sprintf(
      "   omnibus mode: F(%.0f, %.1f) = %.2f, p = %.3f   [n=%d obs, %d groups]%s\n",
      av$NumDF[1], av$DenDF[1], av$`F value`[1], av$`Pr(>F)`[1], n, ng, note))
  } else {
    cat(sprintf("   omnibus mode: (df unavailable)   [n=%d obs, %d groups]%s\n",
                n, ng, note))
  }

  # Total SD = sqrt(sum of variance components incl. residual): the denominator
  # for a Westfall-style standardized d (residual SD alone would inflate d).
  vc <- as.data.frame(VarCorr(m))
  total_sd <- sqrt(sum(vc$vcov))
  emm <- emmeans(m, ~ mode, lmer.df = "satterthwaite")
  mkc <- function(a, b) { v <- setNames(rep(0, length(lev)), lev)
                          v[a] <- 1; v[b] <- -1; v }
  cdef <- list()
  for (p in PAIRS)
    if (all(p %in% lev)) cdef[[paste(p[1], "-", p[2])]] <- mkc(p[1], p[2])
  if (length(cdef) == 0) { cat("   no planned contrast available yet\n")
                           return(invisible()) }
  cs <- summary(contrast(emm, method = cdef, adjust = "holm"),
                infer = c(TRUE, TRUE))
  for (i in seq_len(nrow(cs))) {
    est <- cs$estimate[i]; lo <- cs$lower.CL[i]; hi <- cs$upper.CL[i]
    dir <- if (exploratory) "exp"
           else if (!is.na(lo) && lo > 0) "PSSP>"
           else if (!is.na(hi) && hi < 0) "PSSP<" else "ns"
    cat(sprintf("     %-14s est=%+.2f [%+.2f, %+.2f]  d=%+.2f  p_holm=%.3f  %s\n",
                as.character(cs$contrast[i]), est, lo, hi,
                est / total_sd, cs$p.value[i], dir))
  }
  gv <- function(g) { v <- vc$vcov[vc$grp == g]; if (length(v)) v[1] else NA }
  cat(sprintf(
    "   var: group=%.3f  participant=%.3f  residual=%.3f  (total SD=%.3f)\n",
    gv("group"), gv("participant"), sigma(m)^2, total_sd))
  # Residual normality (LMM assumes ~normal residuals). Discrete scales such as
  # PTL may fail this; if so prefer Yes-Rate / treat PTL descriptively.
  rr <- tryCatch(residuals(m), error = function(e) NULL)
  if (!is.null(rr) && length(rr) >= 3 && length(rr) <= 5000) {
    sw <- tryCatch(shapiro.test(rr), error = function(e) NULL)
    if (!is.null(sw))
      cat(sprintf("   resid normality (Shapiro-Wilk): W=%.3f, p=%.3f%s\n",
                  sw$statistic, sw$p.value,
                  if (sw$p.value < 0.05)
                    "  (non-normal -- interpret with care)" else ""))
  }
}

for (nm in names(SCALES)) fit_one(nm, SCALES[[nm]])
cat("\n")
