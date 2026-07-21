import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { z } from "zod";
import { PaginatedList } from "@/components/paginated-list";

const push = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/candidates",
  useSearchParams: () => searchParams,
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: vi.fn(),
}));

import { apiGet } from "@/lib/api-client";

const mockedApiGet = vi.mocked(apiGet);
const itemSchema = z.object({ id: z.number() });

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PaginatedList
        queryKey={["test"]}
        upstreamPath="/api/v1/test"
        itemSchema={itemSchema}
        render={(rows) => (
          <ul>
            {rows.map((r) => (
              <li key={r.id}>item-{r.id}</li>
            ))}
          </ul>
        )}
      />
    </QueryClientProvider>,
  );
}

describe("PaginatedList", () => {
  beforeEach(() => {
    push.mockClear();
    searchParams = new URLSearchParams();
    mockedApiGet.mockReset();
  });

  it("reads page/page_size from the URL, calls apiGet, and renders the total count", async () => {
    mockedApiGet.mockResolvedValue({ items: [{ id: 1 }, { id: 2 }], page: 1, page_size: 20, total: 2 });
    renderList();
    await waitFor(() => expect(screen.getByText("item-1")).toBeInTheDocument());
    expect(mockedApiGet).toHaveBeenCalledWith(
      "/api/v1/test",
      { page: "1", page_size: "20" },
      expect.anything(),
    );
    expect(screen.getByText(/共 2 条/)).toBeInTheDocument();
  });

  it("disables 下一页 on the last page and pushes the previous page onto the URL", async () => {
    searchParams = new URLSearchParams("page=2");
    mockedApiGet.mockResolvedValue({ items: [{ id: 3 }], page: 2, page_size: 20, total: 21 });
    renderList();
    await waitFor(() => expect(screen.getByText("item-3")).toBeInTheDocument());

    expect(screen.getByText("下一页")).toBeDisabled();

    await userEvent.setup().click(screen.getByText("上一页"));
    expect(push).toHaveBeenCalledWith("/candidates?page=1");
  });
});
