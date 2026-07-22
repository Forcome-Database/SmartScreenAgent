import type { Page } from "@playwright/test";

// Response fixtures used to stub the BROWSER -> Next `/api/proxy/**` calls in
// e2e specs. Playwright's `page.route` only intercepts requests issued by the
// page itself; it can never intercept the Next server's own outbound fetch to
// FastAPI. Stubbing `/api/proxy/**` therefore bypasses the real proxy route
// handler (src/app/api/proxy/[...path]/route.ts) entirely — these fixtures
// are shaped to match the zod response schemas in src/lib/schemas.ts
// (CandidateListItem / CandidateDetail / ScoreDetail), not a live backend.

export const CANDIDATE_LIST_PAGE = {
  items: [{ candidate_id: 7, created_at: "2026-07-21T00:00:00Z", latest_state: "ready", scored_jd_codes: ["FT"] }],
  page: 1,
  page_size: 20,
  total: 1,
};

export const EMPTY_CANDIDATE_LIST_PAGE = { items: [], page: 1, page_size: 20, total: 0 };

export const CANDIDATE_DETAIL = {
  candidate_id: 7,
  name: "张三",
  phone: "138",
  email: "a@b.c",
  age: 30,
  education: "本科",
  experiences: [],
  source: "upload",
  created_at: "2026-07-21T00:00:00Z",
  scores: [{ score_id: 9, jd_code: "FT", total_score: 82, grade: "L4", rule_version: "v1" }],
};

export const SCORE_DETAIL = {
  score_id: 9,
  candidate_id: 7,
  jd_code: "FT",
  rule_version: "v1",
  total_score: 82,
  grade: "L4",
  hard_filter_result: { passed: true },
  rule_dimensions: {},
  judge_dimensions: {
    independence: { score: 8, evidence_quotes: ["独立负责北美客户"], reasoning: "强" },
  },
};

/** Stubs the list/detail/scorecard calls exercised by the golden-path spec. */
export async function stubCandidateFlow(page: Page): Promise<void> {
  await page.route("**/api/proxy/api/v1/candidates?**", (route) =>
    route.fulfill({ status: 200, json: CANDIDATE_LIST_PAGE }),
  );
  await page.route("**/api/proxy/api/v1/candidates/7", (route) =>
    route.fulfill({ status: 200, json: CANDIDATE_DETAIL }),
  );
  await page.route("**/api/proxy/api/v1/candidates/7/scores/9", (route) =>
    route.fulfill({ status: 200, json: SCORE_DETAIL }),
  );
}

/** Stubs an empty candidate list, e.g. for a11y checks on the list's empty state. */
export async function stubEmptyCandidateList(page: Page): Promise<void> {
  await page.route("**/api/proxy/api/v1/candidates?**", (route) =>
    route.fulfill({ status: 200, json: EMPTY_CANDIDATE_LIST_PAGE }),
  );
}
