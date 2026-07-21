import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";

export function DataState({
  isLoading,
  error,
  isEmpty,
  emptyText = "暂无数据",
  onRetry,
  children,
}: {
  isLoading: boolean;
  error?: { message: string } | null;
  isEmpty?: boolean;
  emptyText?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  if (isLoading)
    return (
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  if (error)
    return (
      <div role="alert" className="flex flex-col items-start gap-2 rounded-md border border-destructive/40 p-4">
        <p className="text-destructive">{error.message}</p>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            重试
          </Button>
        ) : null}
      </div>
    );
  if (isEmpty) return <p className="text-muted-foreground p-4">{emptyText}</p>;
  return <>{children}</>;
}
