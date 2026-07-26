"use client";

import * as React from "react";
import { MenuIcon } from "lucide-react";

import { NavLinks } from "@/components/app-sidebar";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

export function MobileAppNav({ role }: { role: string }) {
  const [open, setOpen] = React.useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        aria-label="打开导航菜单"
        className="inline-flex size-11 items-center justify-center rounded-md border text-muted-foreground transition-colors duration-150 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none lg:hidden motion-reduce:transition-none"
      >
        <MenuIcon className="size-4" />
      </SheetTrigger>
      <SheetContent side="left">
        <SheetTitle>导航</SheetTitle>
        <SheetDescription>选择要打开的工作区。</SheetDescription>
        {/* Closing on navigate keeps the sheet from covering the page the user
            just asked for. */}
        <NavLinks role={role} onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
  );
}
