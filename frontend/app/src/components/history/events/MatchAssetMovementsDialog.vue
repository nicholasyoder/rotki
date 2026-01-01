<script setup lang="ts">
import type { HistoryEventEntryWithMeta } from '@/types/history/events/schemas';
import PotentialMatchesDialog from '@/components/history/events/PotentialMatchesDialog.vue';
import UnmatchedMovementsList from '@/components/history/events/UnmatchedMovementsList.vue';
import CardTitle from '@/components/typography/CardTitle.vue';
import { useHistoryEventsApi } from '@/composables/api/history/events';
import {
  type UnmatchedAssetMovement,
  useUnmatchedAssetMovements,
} from '@/composables/history/events/use-unmatched-asset-movements';

const modelValue = defineModel<boolean>({ required: true });

const emit = defineEmits<{
  refresh: [];
}>();

const { t } = useI18n({ useScope: 'global' });

const {
  fetchUnmatchedAssetMovements,
  loading,
  unmatchedMovements,
} = useUnmatchedAssetMovements();

const { matchAssetMovements } = useHistoryEventsApi();

const selectedMovement = ref<UnmatchedAssetMovement>();
const showPotentialMatchesDialog = ref<boolean>(false);
const ignoreLoading = ref<boolean>(false);

function getEventEntry(movement: UnmatchedAssetMovement): HistoryEventEntryWithMeta {
  const events = Array.isArray(movement.events) ? movement.events : [movement.events];
  return events[0];
}

function selectMovement(movement: UnmatchedAssetMovement): void {
  set(selectedMovement, movement);
  set(showPotentialMatchesDialog, true);
}

async function ignoreMovement(movement: UnmatchedAssetMovement): Promise<void> {
  set(ignoreLoading, true);
  try {
    const eventEntry = getEventEntry(movement);
    await matchAssetMovements(eventEntry.entry.identifier);
    await fetchUnmatchedAssetMovements();
    emit('refresh');
  }
  finally {
    set(ignoreLoading, false);
  }
}

function onMatched(): void {
  set(selectedMovement, undefined);
  emit('refresh');
}

function closeDialog(): void {
  set(modelValue, false);
}

onMounted(async () => {
  await fetchUnmatchedAssetMovements();
});
</script>

<template>
  <RuiDialog
    v-model="modelValue"
    max-width="900"
  >
    <RuiCard content-class="!py-0 max-h-[calc(100vh-250px)]">
      <template #custom-header>
        <div class="flex items-center justify-between w-full px-4 pt-2">
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

      <div
        v-if="loading"
        class="flex items-center justify-center p-8"
      >
        <RuiProgress
          circular
          variant="indeterminate"
        />
      </div>

      <UnmatchedMovementsList
        v-else
        :movements="unmatchedMovements"
        :ignore-loading="ignoreLoading"
        @ignore="ignoreMovement($event)"
        @select="selectMovement($event)"
      />

      <template #footer>
        <div class="w-full flex justify-end gap-2">
          <RuiButton
            variant="text"
            @click="closeDialog()"
          >
            {{ t('common.actions.close') }}
          </RuiButton>
        </div>
      </template>
    </RuiCard>
  </RuiDialog>

  <PotentialMatchesDialog
    v-if="selectedMovement"
    v-model="showPotentialMatchesDialog"
    :movement="selectedMovement"
    @matched="onMatched()"
  />
</template>
