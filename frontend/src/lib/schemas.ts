import { z } from "zod";

export const LoginResult = z.object({ token: z.string(), display_name: z.string(), role: z.string() });

export const RankedCandidate = z.object({
  candidate_id: z.number(),
  score_id: z.number(),
  total_score: z.number(),
  grade: z.string(),
  rule_version: z.string(),
  scored_at: z.string(),
});
export const CandidateListItem = z.object({
  candidate_id: z.number(),
  created_at: z.string(),
  latest_state: z.string().nullable(),
  scored_jd_codes: z.array(z.string()),
});
export const CandidateScoreSummary = z.object({
  score_id: z.number(),
  jd_code: z.string(),
  total_score: z.number(),
  grade: z.string(),
  rule_version: z.string(),
});
export const CandidateDetail = z.object({
  candidate_id: z.number(),
  name: z.string(),
  phone: z.string().nullable(),
  email: z.string().nullable(),
  age: z.number().nullable(),
  education: z.string().nullable(),
  experiences: z.array(z.record(z.string(), z.unknown())),
  source: z.string(),
  created_at: z.string(),
  scores: z.array(CandidateScoreSummary),
});
export const ScoreDetail = z.object({
  score_id: z.number(),
  candidate_id: z.number(),
  jd_code: z.string(),
  rule_version: z.string(),
  total_score: z.number(),
  grade: z.string(),
  hard_filter_result: z.record(z.string(), z.unknown()),
  rule_dimensions: z.record(z.string(), z.unknown()),
  judge_dimensions: z.record(z.string(), z.unknown()).nullable(),
});
export const JobStatus = z.object({
  job_id: z.string(),
  state: z.string(),
  candidate_id: z.number().nullable().optional(),
  last_error_code: z.string().nullable().optional(),
});
export const BatchStatus = z.object({
  batch_id: z.string(),
  jobs: z.array(z.object({ job_id: z.string(), state: z.string(), filename: z.string().optional() })).optional(),
});

export function pageEnvelope<T extends z.ZodTypeAny>(item: T) {
  return z.object({ items: z.array(item), page: z.number(), page_size: z.number(), total: z.number() });
}

const TERMINAL_JOB_STATES = new Set(["ready", "completed", "retryable_failed", "terminal_failed", "deleted"]);
export function isTerminalJobState(state: string): boolean {
  return TERMINAL_JOB_STATES.has(state);
}
