import type { ComputedRef, Ref } from 'vue';

export interface ColumnClassConfig {
  class?: string;
  cellClass?: string;
}

export function usePinnedColumnClass(isPinned: Ref<boolean | undefined>): ComputedRef<ColumnClassConfig> {
  return computed<ColumnClassConfig>(() =>
    get(isPinned) ? { cellClass: '!px-2 !text-xs', class: '!px-2' } : {},
  );
}
