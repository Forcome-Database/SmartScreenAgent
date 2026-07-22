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
  state: z.string(),
  attempts: z.number(),
  last_error_code: z.string().nullable(),
  candidate_id: z.number().nullable(),
  score_id: z.number().nullable(),
  batch_id: z.string().nullable(),
});
export const BatchStatus = z.object({
  total: z.number(),
  by_state: z.record(z.string(), z.number()),
});
export const UploadResponse = z.object({
  job_id: z.number(),
  batch_id: z.string().nullable(),
  state: z.string(),
});
export const BatchResponse = z.object({
  batch_id: z.string(),
  jobs: z.array(
    z.object({
      job_id: z.number().nullable(),
      state: z.string(),
      error_code: z.string().nullable().optional(),
    }),
  ),
});

export function pageEnvelope<T extends z.ZodTypeAny>(item: T) {
  return z.object({ items: z.array(item), page: z.number(), page_size: z.number(), total: z.number() });
}

const TERMINAL_JOB_STATES = new Set(["ready", "completed", "terminal_failed", "deleted"]);
export function isTerminalJobState(state: string): boolean {
  return TERMINAL_JOB_STATES.has(state);
}

export const FeedbackItem = z.object({
  id: z.number(),
  score_id: z.number(),
  reviewer_user_id: z.number(),
  reviewer_display_name: z.string(),
  decision: z.string(),
  reason: z.string().nullable(),
  ai_agreed: z.boolean().nullable(),
  created_at: z.string(),
  updated_at: z.string().nullable(),
});
export const FeedbackList = z.array(FeedbackItem);
