<script setup lang="ts">
import type { TabContent } from '@/types/tabs';
import TabNavigation from '@/components/helper/TabNavigation.vue';
import TablePageLayout from '@/components/layout/TablePageLayout.vue';
import { useAppRoutes } from '@/router/routes';

definePage({
  name: 'asset-manager-more',
  redirect: '/asset-manager/more/cex-mapping',
});

const { appRoutes } = useAppRoutes();

const tabs = computed<TabContent[]>(() => {
  const Routes = get(appRoutes);
  return [
    Routes.ASSET_MANAGER_CEX_MAPPING,
    Routes.ASSET_MANAGER_NEWLY_DETECTED,
    Routes.ASSET_MANAGER_MISSING_MAPPINGS,
  ];
});

const { t } = useI18n();
</script>

<template>
  <TablePageLayout :title="[t('navigation_menu.manage_assets'), t('navigation_menu.manage_assets_sub.more')]">
    <div class="flex justify-between">
      <TabNavigation
        :tabs="tabs"
        class="asset-manager-more"
        hide-router-view
        child
      />
    </div>
    <RouterView />
  </TablePageLayout>
</template>
