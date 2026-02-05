<script setup lang="ts">
import { useAreaVisibilityStore } from '@/store/session/visibility';

const ReportActionableCard = defineAsyncComponent(() => import('@/components/profitloss/ReportActionableCard.vue'));
const MatchAssetMovementsPinned = defineAsyncComponent(() => import('@/components/history/events/MatchAssetMovementsPinned.vue'));

const { pinned, showPinned } = storeToRefs(useAreaVisibilityStore());

const { isLgAndDown } = useBreakpoint();

const component = computed<typeof ReportActionableCard | typeof MatchAssetMovementsPinned | undefined>(() => {
  const pinnedValue = get(pinned);
  if (pinnedValue && pinnedValue.name === 'report-actionable-card')
    return ReportActionableCard;

  if (pinnedValue && pinnedValue.name === 'match-asset-movements-pinned')
    return MatchAssetMovementsPinned;

  return undefined;
});
</script>

<template>
  <RuiNavigationDrawer
    v-model="showPinned"
    :temporary="isLgAndDown"
    width="520px"
    position="right"
    class="border-l border-rui-grey-300 dark:border-rui-grey-800 z-[6]"
  >
    <div>
      <Component
        :is="component"
        v-if="pinned && component"
        v-bind="pinned.props"
      />
    </div>
  </RuiNavigationDrawer>
</template>
