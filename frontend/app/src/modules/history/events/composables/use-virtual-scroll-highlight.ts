import type { TablePaginationData } from '@rotki/ui-library';
import type { ComputedRef, Ref } from 'vue';
import type { VirtualRow } from './use-virtual-rows';
import type { HighlightType } from '@/composables/history/events/use-history-events-filters';
import type { HistoryEventEntry } from '@/types/history/events/schemas';
import { startPromise } from '@shared/utils';
import { useMediaQuery, useVirtualList, type UseVirtualListReturn } from '@vueuse/core';

// Number of extra items to render above/below the visible viewport for smoother scrolling
const OVERSCAN_COUNT = 15;

interface UseVirtualScrollHighlightOptions {
  flattenedRows: ComputedRef<VirtualRow[]>;
  getRowHeight: (index: number) => number;
  getCardHeight: (index: number) => number;
  highlightedIdentifiers: ComputedRef<string[] | undefined>;
  highlightTypes: ComputedRef<Record<string, HighlightType> | undefined>;
  loading: Ref<boolean>;
  pagination: Ref<TablePaginationData>;
}

interface UseVirtualScrollHighlightReturn {
  containerProps: UseVirtualListReturn<VirtualRow>['containerProps'];
  getHighlightType: (event: HistoryEventEntry) => HighlightType | undefined;
  getSwapHighlightType: (swapEvents: HistoryEventEntry[]) => HighlightType | undefined;
  isCardLayout: Ref<boolean>;
  isHighlighted: (event: HistoryEventEntry) => boolean;
  isSwapHighlighted: (swapEvents: HistoryEventEntry[]) => boolean;
  virtualList: UseVirtualListReturn<VirtualRow>['list'];
  wrapperProps: UseVirtualListReturn<VirtualRow>['wrapperProps'];
}

/**
 * Composable for managing virtual scroll highlighting and auto-scroll behavior.
 *
 * Handles:
 * - Virtual list setup with dynamic item heights
 * - Auto-scrolling to highlighted events when data loads
 * - Smart scroll positioning to show multiple highlights when possible
 * - Highlight state helpers for single events and swap/movement groups
 */
export function useVirtualScrollHighlight(options: UseVirtualScrollHighlightOptions): UseVirtualScrollHighlightReturn {
  const {
    flattenedRows,
    getRowHeight,
    getCardHeight,
    highlightedIdentifiers,
    highlightTypes,
    loading,
    pagination,
  } = options;

  // Responsive breakpoint for card layout (860px)
  const isCardLayout = useMediaQuery('(max-width: 860px)');

  // Use card heights for mobile layout
  const getItemHeight = computed<(index: number) => number>(() =>
    get(isCardLayout) ? getCardHeight : getRowHeight,
  );

  // Virtual list with dynamic item heights
  const { containerProps, list: virtualList, wrapperProps, scrollTo } = useVirtualList(flattenedRows, {
    itemHeight: (index: number) => get(getItemHeight)(index),
    overscan: OVERSCAN_COUNT,
  });

  const hasScrolledToHighlight = ref<boolean>(false);
  const pendingHighlightScroll = ref<boolean>(false);

  // Reset scroll state when highlighted identifiers change
  watch(highlightedIdentifiers, () => {
    set(hasScrolledToHighlight, false);
    set(pendingHighlightScroll, true);
  });

  // Scroll to top only when page changes, unless we have a pending highlight scroll
  watch(pagination, (current, previous) => {
    if (!previous)
      return;

    if (current.page !== previous.page && !get(pendingHighlightScroll)) {
      scrollTo(0);
    }
  });

  /**
   * Find the row index for a given identifier.
   */
  function findRowIndexForIdentifier(rows: VirtualRow[], identifier: string): number {
    return rows.findIndex((row) => {
      if (row.type === 'event-row' || row.type === 'group-header')
        return row.data.identifier.toString() === identifier;
      if (row.type === 'swap-row' || row.type === 'matched-movement-row')
        return row.events.some(e => e.identifier.toString() === identifier);
      return false;
    });
  }

  /**
   * Check if a row index is currently visible in the viewport.
   * Uses the virtualList which contains currently rendered items.
   * We exclude overscan buffer to check actual visibility.
   */
  function isRowVisible(rowIndex: number): boolean {
    const list = get(virtualList);
    if (list.length === 0)
      return false;

    // Get the range of rendered indices
    const renderedIndices = list.map(item => item.index);
    const minRendered = Math.min(...renderedIndices);
    const maxRendered = Math.max(...renderedIndices);

    // Exclude overscan buffer each side to get actual visible range
    // This is an approximation since overscan might not be fully used at edges
    const visibleMin = minRendered + Math.min(OVERSCAN_COUNT, Math.floor(list.length / 4));
    const visibleMax = maxRendered - Math.min(OVERSCAN_COUNT, Math.floor(list.length / 4));

    return rowIndex >= visibleMin && rowIndex <= visibleMax;
  }

  /**
   * Calculate scroll position when both yellow and green highlights exist.
   *
   * Logic:
   * - If distance <= 3 rows: Show both (position earlier one at top)
   * - If distance > 3 rows:
   *   - Green after yellow: Green at bottom of viewport
   *   - Green before yellow: Green at top of viewport
   */
  function calculateScrollPosition(
    primaryIndex: number,
    secondaryIndex: number,
  ): number {
    // Card layout has taller rows, so fewer fit in viewport
    const isCard = get(isCardLayout);
    const estimatedViewportRows = isCard ? 3 : 10;
    const distance = Math.abs(secondaryIndex - primaryIndex);

    // If close enough, show both by positioning earlier one at top
    if (distance <= (isCard ? 1 : 3)) {
      const earlierIndex = Math.min(primaryIndex, secondaryIndex);
      return Math.max(0, earlierIndex);
    }

    // Distance > 3: position based on green's location relative to yellow
    if (secondaryIndex > primaryIndex) {
      // Green is after yellow - put green at bottom of viewport
      // Use smaller offset for card layout since fewer rows fit
      const bottomOffset = isCard ? 1 : 4;
      return Math.max(0, secondaryIndex - estimatedViewportRows + bottomOffset);
    }
    else {
      // Green is before yellow - put green at top of viewport
      return Math.max(0, secondaryIndex);
    }
  }

  // Auto-scroll to highlighted events (wait for loading to complete)
  watchDebounced([flattenedRows, highlightedIdentifiers, loading], ([rows, identifiers, isLoading]) => {
    // Wait for loading to complete before attempting to scroll
    if (isLoading || !identifiers || identifiers.length === 0 || rows.length === 0 || get(hasScrolledToHighlight))
      return;

    // Find indices for all highlighted identifiers
    const indices = identifiers
      .map(id => ({ id, index: findRowIndexForIdentifier(rows, id) }))
      .filter(item => item.index >= 0);

    if (indices.length === 0)
      return;

    let scrollIndex: number | undefined;

    if (indices.length === 1) {
      // Single highlight (yellow) - always scroll to ensure it's visible
      // This is the primary navigation case when clicking "Show in history events"
      const targetIndex = indices[0].index;
      // For card layout with taller rows, scroll one item earlier to ensure visibility
      scrollIndex = get(isCardLayout) && targetIndex > 0 ? targetIndex + 2 : targetIndex;
    }
    else if (indices.length === 2) {
      // Two highlights (yellow + green) - try to show both
      // Primary (yellow) is first, secondary (green) is second/last
      const primaryIndex = indices[0].index;
      const secondaryIndex = indices[1].index;

      // Always scroll when adding the green highlight since it's an intentional navigation
      // The calculateScrollPosition will try to show both if possible
      scrollIndex = calculateScrollPosition(primaryIndex, secondaryIndex);
    }
    else {
      // More than 2 highlights - scroll to the last one if not visible
      const lastIndex = indices.at(-1)!.index;
      if (!isRowVisible(lastIndex)) {
        scrollIndex = lastIndex;
      }
    }

    set(hasScrolledToHighlight, true);
    set(pendingHighlightScroll, false);

    // Only scroll if we determined a scroll position is needed
    if (scrollIndex !== undefined) {
      startPromise(nextTick(() => {
        scrollTo(scrollIndex);
      }));
    }
  }, { debounce: 200 });

  /**
   * Check if an event should be highlighted.
   */
  function isHighlighted(event: HistoryEventEntry): boolean {
    const identifiers = get(highlightedIdentifiers);
    if (!identifiers || identifiers.length === 0)
      return false;
    return identifiers.includes(event.identifier.toString());
  }

  /**
   * Get the highlight type for an event.
   */
  function getHighlightType(event: HistoryEventEntry): HighlightType | undefined {
    const types = get(highlightTypes);
    if (!types)
      return undefined;
    return types[event.identifier.toString()];
  }

  /**
   * Check if any event in a swap/movement group should be highlighted.
   */
  function isSwapHighlighted(swapEvents: HistoryEventEntry[]): boolean {
    const identifiers = get(highlightedIdentifiers);
    if (!identifiers || identifiers.length === 0)
      return false;
    return swapEvents.some(e => identifiers.includes(e.identifier.toString()));
  }

  /**
   * Get the highlight type for a swap/movement group (returns the first matched type).
   */
  function getSwapHighlightType(swapEvents: HistoryEventEntry[]): HighlightType | undefined {
    const types = get(highlightTypes);
    if (!types)
      return undefined;
    for (const event of swapEvents) {
      const type = types[event.identifier.toString()];
      if (type)
        return type;
    }
    return undefined;
  }

  return {
    containerProps,
    getHighlightType,
    getSwapHighlightType,
    isCardLayout,
    isHighlighted,
    isSwapHighlighted,
    virtualList,
    wrapperProps,
  };
}
