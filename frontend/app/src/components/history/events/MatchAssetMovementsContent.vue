<script setup lang="ts">
import type { UnmatchedAssetMovement } from '@/composables/history/events/use-unmatched-asset-movements';
import AssetMovementMatchingSettingsMenu from '@/components/history/events/AssetMovementMatchingSettingsMenu.vue';
import UnmatchedMovementsList from '@/components/history/events/UnmatchedMovementsList.vue';

const selectedUnmatched = defineModel<string[]>('selectedUnmatched', { required: true });

const selectedIgnored = defineModel<string[]>('selectedIgnored', { required: true });

const props = defineProps<{
  unmatchedMovements: UnmatchedAssetMovement[];
  ignoredMovements: UnmatchedAssetMovement[];
  fiatMovements: UnmatchedAssetMovement[];
  highlightedGroupIdentifier?: string;
  loading?: boolean;
  ignoredLoading?: boolean;
  ignoreLoading?: boolean;
  autoMatchLoading?: boolean;
  isPinned?: boolean;
}>();

const emit = defineEmits<{
  'close': [];
  'confirm-ignore-all-fiat': [];
  'confirm-ignore-selected': [];
  'confirm-unignore-selected': [];
  'ignore': [movement: UnmatchedAssetMovement];
  'pin': [];
  'restore': [movement: UnmatchedAssetMovement];
  'select': [movement: UnmatchedAssetMovement];
  'show-in-events': [movement: UnmatchedAssetMovement];
  'trigger-auto-match': [];
}>();

const { t } = useI18n({ useScope: 'global' });

const activeTab = ref<number>(0);

const buttonSize = computed<'sm' | undefined>(() => props.isPinned ? 'sm' : undefined);
</script>

<template>
  <RuiTabs
    v-model="activeTab"
    class="border-b border-default"
    color="primary"
  >
    <RuiTab>
      {{ t('asset_movement_matching.tabs.unmatched') }}
      <RuiChip
        v-if="unmatchedMovements.length > 0"
        color="primary"
        size="sm"
        class="ml-2 !px-0.5 !py-0"
      >
        {{ unmatchedMovements.length }}
      </RuiChip>
    </RuiTab>
    <RuiTab>
      {{ t('asset_movement_matching.tabs.ignored') }}
      <RuiChip
        v-if="ignoredMovements.length > 0"
        color="secondary"
        size="sm"
        class="ml-2 !px-0.5 !py-0"
      >
        {{ ignoredMovements.length }}
      </RuiChip>
    </RuiTab>
  </RuiTabs>

  <RuiTabItems
    v-model="activeTab"
    :class="isPinned ? 'my-4 px-4' : 'my-4'"
  >
    <RuiTabItem>
      <UnmatchedMovementsList
        v-model:selected="selectedUnmatched"
        :movements="unmatchedMovements"
        :highlighted-group-identifier="highlightedGroupIdentifier"
        :ignore-loading="ignoreLoading"
        :is-pinned="isPinned"
        :loading="loading"
        @ignore="emit('ignore', $event)"
        @pin="emit('pin')"
        @select="emit('select', $event)"
        @show-in-events="emit('show-in-events', $event)"
      />
    </RuiTabItem>
    <RuiTabItem>
      <UnmatchedMovementsList
        v-model:selected="selectedIgnored"
        :movements="ignoredMovements"
        :highlighted-group-identifier="highlightedGroupIdentifier"
        :loading="ignoredLoading"
        :ignore-loading="ignoreLoading"
        :is-pinned="isPinned"
        show-restore
        @pin="emit('pin')"
        @restore="emit('restore', $event)"
        @show-in-events="emit('show-in-events', $event)"
      />
    </RuiTabItem>
  </RuiTabItems>

  <div
    class="w-full flex justify-between gap-2"
    :class="isPinned ? 'p-2 border-t border-default' : 'pb-4'"
  >
    <div
      v-if="activeTab === 0"
      class="flex gap-2"
      :class="{ 'flex-wrap': isPinned }"
    >
      <RuiButton
        variant="outlined"
        color="primary"
        :size="buttonSize"
        :class="{ 'h-[30px]': isPinned }"
        :disabled="selectedUnmatched.length === 0 || ignoreLoading"
        :loading="ignoreLoading"
        @click="emit('confirm-ignore-selected')"
      >
        {{ t('asset_movement_matching.actions.ignore_selected') }}
        <RuiChip
          v-if="!isPinned && selectedUnmatched.length > 0"
          size="sm"
          color="primary"
          class="ml-2 !py-0"
        >
          {{ selectedUnmatched.length }}
        </RuiChip>
      </RuiButton>
      <RuiTooltip
        :open-delay="400"
        :popper="{ placement: 'top' }"
        tooltip-class="max-w-80"
      >
        <template #activator>
          <RuiButton
            variant="outlined"
            color="warning"
            :size="buttonSize"
            :class="{ 'h-[30px]': isPinned }"
            :disabled="fiatMovements.length === 0 || ignoreLoading"
            :loading="ignoreLoading"
            @click="emit('confirm-ignore-all-fiat')"
          >
            {{ t('asset_movement_matching.actions.ignore_fiat') }}
          </RuiButton>
        </template>
        {{ t('asset_movement_matching.actions.ignore_fiat_tooltip') }}
      </RuiTooltip>
      <RuiButtonGroup
        color="primary"
        :class="{ 'pl-3': !isPinned }"
        :disabled="autoMatchLoading"
      >
        <RuiTooltip
          :open-delay="400"
          :popper="{ placement: 'top' }"
          tooltip-class="max-w-80"
        >
          <template #activator>
            <RuiButton
              color="primary"
              class="!rounded-r-none"
              :class="isPinned ? 'h-[30px] !px-3' : 'h-9'"
              :disabled="unmatchedMovements.length === 0 || autoMatchLoading"
              :loading="autoMatchLoading"
              @click="emit('trigger-auto-match')"
            >
              {{ t('asset_movement_matching.actions.auto_match') }}
            </RuiButton>
          </template>
          {{ t('asset_movement_matching.actions.auto_match_tooltip') }}
        </RuiTooltip>

        <AssetMovementMatchingSettingsMenu
          :disabled="autoMatchLoading"
          :is-pinned="isPinned"
        />
      </RuiButtonGroup>
    </div>
    <div
      v-else
      class="flex gap-2"
    >
      <RuiButton
        variant="outlined"
        color="primary"
        :size="buttonSize"
        :disabled="selectedIgnored.length === 0 || ignoreLoading"
        :loading="ignoreLoading"
        @click="emit('confirm-unignore-selected')"
      >
        {{ t('asset_movement_matching.actions.unignore_selected') }}
        <RuiChip
          v-if="!isPinned && selectedIgnored.length > 0"
          size="sm"
          color="primary"
          class="ml-2 !py-0"
        >
          {{ selectedIgnored.length }}
        </RuiChip>
      </RuiButton>
    </div>
    <RuiButton
      v-if="!isPinned"
      variant="text"
      @click="emit('close')"
    >
      {{ t('common.actions.close') }}
    </RuiButton>
  </div>
</template>
