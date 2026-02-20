import type { MessageHandler } from '../interfaces';
import type { MoneriumSessionKeyExpiredData } from '@/modules/messaging/types';
import { NotificationCategory, Severity } from '@rotki/common';
import { useMoneriumOAuth } from '@/modules/external-services/monerium/use-monerium-auth';
import { createNotificationHandler } from '@/modules/messaging/utils';
import { Routes } from '@/router/routes';

export function createMoneriumSessionHandler(
  t: ReturnType<typeof useI18n>['t'],
  router: ReturnType<typeof useRouter>,
): MessageHandler<MoneriumSessionKeyExpiredData> {
  const { refreshStatus, setStatus } = useMoneriumOAuth();

  return createNotificationHandler<MoneriumSessionKeyExpiredData>(async (data) => {
    // Backend may have cleared credentials (invalid_grant); update UI state immediately.
    setStatus({ authenticated: false });
    await refreshStatus();

    return {
      action: {
        action: async () => router.push({
          path: Routes.API_KEYS_EXTERNAL_SERVICES.toString(),
          query: { service: 'monerium' },
        }),
        icon: 'lu-arrow-right',
        label: t('external_services.actions.reauthenticate'),
        persist: true,
      },
      category: NotificationCategory.DEFAULT,
      display: true,
      message: data.error,
      severity: Severity.WARNING,
      title: t('notification_messages.monerium_session_key_expired.title'),
    };
  });
}
