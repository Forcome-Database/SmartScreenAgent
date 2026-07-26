import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";
import { useShellHeader } from "@/components/app-session-context";
import { visibleGroups } from "@/components/app-sidebar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/candidates",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

function withProviders(ui: React.ReactElement) {
  return (
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
  );
}

/** The sidebar repeats these labels, so header assertions must be scoped. */
function breadcrumb() {
  return within(screen.getByRole("navigation", { name: "面包屑" }));
}

function Report({ stamp }: { stamp?: string }) {
  useShellHeader({
    breadcrumbs: [{ label: "报表", href: "/reports" }, { label: "运营成本" }],
    lastRefreshedAt: stamp,
  });
  return <p>报表内容</p>;
}

describe("navigation visibility", () => {
  it("groups links under the three workspaces", () => {
    const titles = visibleGroups("admin").map((group) => group.title);

    expect(titles).toEqual(["招聘", "复核与规则", "运营与质量"]);
  });

  it("hides tenant-wide cost from plain reviewers but keeps quality visible", () => {
    const hrLinks = visibleGroups("hr").flatMap((group) =>
      group.links.map((link) => link.href),
    );

    expect(hrLinks).not.toContain("/reports/operations");
    expect(hrLinks).toEqual(
      expect.arrayContaining([
        "/reports/quality",
        "/reports/batch",
        "/reports/cross-checks",
      ]),
    );
  });

  it.each(["hr_lead", "admin"])("shows cost to %s", (role) => {
    const links = visibleGroups(role).flatMap((group) =>
      group.links.map((link) => link.href),
    );

    expect(links).toContain("/reports/operations");
  });

  it("preserves every pre-WP7 route", () => {
    const links = visibleGroups("admin").flatMap((group) =>
      group.links.map((link) => link.href),
    );

    for (const href of [
      "/candidates",
      "/upload",
      "/reports/feedback",
      "/golden-set",
      "/reports/baseline",
    ]) {
      expect(links).toContain(href);
    }
  });
});

describe("shell chrome", () => {
  it("marks the current route as the active page", () => {
    render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <p>内容</p>
        </AppShell>,
      ),
    );

    const active = screen.getAllByRole("link", { name: /候选人/ })[0];
    expect(active).toHaveAttribute("aria-current", "page");
  });

  it("shows the signed-in user and a logout control", () => {
    render(
      withProviders(
        <AppShell displayName="张三" role="hr">
          <p>内容</p>
        </AppShell>,
      ),
    );

    expect(screen.getByText("张三（hr）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /退出/ })).toBeInTheDocument();
  });

  it("opens the mobile menu from an accessibly named trigger", async () => {
    const user = userEvent.setup();
    render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <p>内容</p>
        </AppShell>,
      ),
    );

    await user.click(screen.getByRole("button", { name: "打开导航菜单" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("导航")).toBeInTheDocument();
  });

  it("closes the mobile menu with Escape", async () => {
    const user = userEvent.setup();
    render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <p>内容</p>
        </AppShell>,
      ),
    );

    await user.click(screen.getByRole("button", { name: "打开导航菜单" }));
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("contextual header metadata", () => {
  it("renders the breadcrumb and refresh stamp a page registers", async () => {
    render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <Report stamp="12:00" />
        </AppShell>,
      ),
    );

    await waitFor(() =>
      expect(breadcrumb().getByText("运营成本")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("last-refreshed")).toHaveTextContent("12:00");
  });

  it("updates the stamp when the page's query refreshes", async () => {
    const { rerender } = render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <Report stamp="12:00" />
        </AppShell>,
      ),
    );
    await screen.findByTestId("last-refreshed");

    rerender(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <Report stamp="12:05" />
        </AppShell>,
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("last-refreshed")).toHaveTextContent("12:05"),
    );
  });

  it("restores the default header when the report unmounts", async () => {
    const { rerender } = render(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <Report stamp="12:00" />
        </AppShell>,
      ),
    );
    await screen.findByTestId("last-refreshed");

    rerender(
      withProviders(
        <AppShell displayName="张三" role="admin">
          <p>别的页面</p>
        </AppShell>,
      ),
    );

    // A stale "last refreshed" above unrelated content would be a lie.
    await waitFor(() =>
      expect(screen.queryByTestId("last-refreshed")).not.toBeInTheDocument(),
    );
    expect(breadcrumb().getByText("SmartScreen")).toBeInTheDocument();
  });
});
