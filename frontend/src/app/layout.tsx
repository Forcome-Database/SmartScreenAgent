import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/lib/query";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = { title: "HR 工作台", description: "智能简历筛选 HR 工作台" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
        <Toaster />
      </body>
    </html>
  );
}
