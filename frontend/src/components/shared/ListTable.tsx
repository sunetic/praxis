import type { ReactNode } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { TableCell, TableRow } from "@/components/ui/table"

type ListTableProps = {
  children: ReactNode
  className?: string
}

type ListTableLoadingRowsProps = {
  rowCount?: number
  columnCount: number
}

function ListTable({ children, className }: ListTableProps) {
  return <div className={className ?? "overflow-hidden rounded-lg border border-border"}>{children}</div>
}

function ListTableLoadingRows({ rowCount = 6, columnCount }: ListTableLoadingRowsProps) {
  return Array.from({ length: rowCount }).map((_, rowIndex) => (
    <TableRow key={rowIndex}>
      {Array.from({ length: columnCount }).map((__, columnIndex) => (
        <TableCell key={columnIndex}>
          <Skeleton className="h-4 w-full" />
        </TableCell>
      ))}
    </TableRow>
  ))
}

export { ListTable, ListTableLoadingRows }
