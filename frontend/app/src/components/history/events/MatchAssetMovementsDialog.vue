<script setup lang="ts">
import type { Nullable } from '@rotki/common';
import type { Pinned } from '@/types/session';
import MatchAssetMovementsContent from '@/components/history/events/MatchAssetMovementsContent.vue';
import CardTitle from '@/components/typography/CardTitle.vue';
import { useAssetMovementActions } from '@/composables/history/events/use-asset-movement-actions';
import {
  type UnmatchedAssetMovement,
  useUnmatchedAssetMovements,
} from '@/composables/history/events/use-unmatched-asset-movements';
import { useAreaVisibilityStore } from '@/store/session/visibility';

const modelValue = defineModel<boolean>({ default: false });

const emit = defineEmits<{
  'find-match': [movement: UnmatchedAssetMovement];
  'refresh': [];
}>();

const { t } = useI18n({ useScope: 'global' });

const {
  autoMatchLoading,
  ignoredLoading,
  ignoredMovements,
  loading,
  unmatchedMovements,
  refreshUnmatchedAssetMovements,
  triggerAssetMovementAutoMatching,
} = useUnmatchedAssetMovements();

const {
  confirmIgnoreAllFiat,
  confirmIgnoreSelected,
  confirmUnignoreSelected,
  fiatMovements,
  ignoreLoading,
  ignoreMovement,
  restoreMovement,
  selectedIgnored,
  selectedUnmatched,
} = useAssetMovementActions();

const { pinned, showPinned } = storeToRefs(useAreaVisibilityStore());

function selectMovement(movement: UnmatchedAssetMovement): void {
  emit('find-match', movement);
}

function closeDialog(): void {
  set(modelValue, false);
}

function setPinned(pin: Nullable<Pinned>): void {
  set(pinned, pin);
}

function pinSection(highlightedGroupIdentifier?: string): void {
  const pin: Pinned = {
    name: 'match-asset-movements-pinned',
    props: highlightedGroupIdentifier ? { highlightedGroupIdentifier } : {},
  };

  setPinned(pin);
  set(showPinned, true);
  set(modelValue, false);
}

function showInHistoryEvents(movement: UnmatchedAssetMovement): void {
  pinSection(movement.groupIdentifier);
}

onBeforeMount(async () => {
  await refreshUnmatchedAssetMovements();
});
</script>

<template>
  <RuiDialog
    v-model="modelValue"
    max-width="1000"
  >
    <RuiCard
      content-class="!py-0"
      divide
    >
      <template #custom-header>
        <div class="flex items-center justify-between w-full px-4 py-2">
          <CardTitle>
            {{ t('asset_movement_matching.dialog.title') }}
          </CardTitle>
          <RuiButton
            variant="text"
            icon
            @click="closeDialog()"
          >
            <RuiIcon name="lu-x" />
          </RuiButton>
        </div>
      </template>

      <MatchAssetMovementsContent
        v-model:selected-unmatched="selectedUnmatched"
        v-model:selected-ignored="selectedIgnored"
        :unmatched-movements="unmatchedMovements"
        :ignored-movements="ignoredMovements"
        :fiat-movements="fiatMovements"
        :loading="loading"
        :ignored-loading="ignoredLoading"
        :ignore-loading="ignoreLoading"
        :auto-match-loading="autoMatchLoading"
        @close="closeDialog()"
        @confirm-ignore-all-fiat="confirmIgnoreAllFiat()"
        @confirm-ignore-selected="confirmIgnoreSelected()"
        @confirm-unignore-selected="confirmUnignoreSelected()"
        @ignore="ignoreMovement($event)"
        @pin="pinSection()"
        @restore="restoreMovement($event)"
        @select="selectMovement($event)"
        @show-in-events="showInHistoryEvents($event)"
        @trigger-auto-match="triggerAssetMovementAutoMatching()"
      />
    </RuiCard>
  </RuiDialog>
</template>
