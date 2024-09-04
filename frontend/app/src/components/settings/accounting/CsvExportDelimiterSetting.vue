<script setup lang="ts">
import useVuelidate from '@vuelidate/core';
import { helpers, required } from '@vuelidate/validators';
import { toMessages } from '@/utils/validation';

const csvExportDelimiter = ref<string>('');
const isSingleCharacter = (value: string) => value.length === 1;

const { t } = useI18n();

const rules = {
  csvExportDelimiter: {
    required: helpers.withMessage(t('general_settings.csv_export_delimiter.validation.empty'), required),
    singleCharacter: helpers.withMessage(
      t('general_settings.csv_export_delimiter.validation.single_character'),
      isSingleCharacter,
    ),
  },
};

const v$ = useVuelidate(rules, { csvExportDelimiter }, { $autoDirty: true });
const { callIfValid } = useValidation(v$);
const { csvDelimiter } = storeToRefs(useAccountingSettingsStore());

function resetCsvExportDelimiter() {
  set(csvExportDelimiter, get(csvDelimiter));
}

function successMessage(delimiter: string) {
  return t('general_settings.validation.csv_export_delimiter.success', {
    delimiter,
  });
}

onMounted(() => {
  resetCsvExportDelimiter();
});
</script>

<template>
  <div>
    <SettingsOption
      #default="{ error, success, update, updateImmediate }"
      setting="csvExportDelimiter"
      :error-message="t('general_settings.validation.csv_export_delimiter.error')"
      :success-message="successMessage"
      class="flex items-start gap-4"
      @finished="resetCsvExportDelimiter()"
    >
      <RuiTextField
        v-model="csvExportDelimiter"
        variant="outlined"
        color="primary"
        class="general-settings__fields__csv-export-delimiter flex-1"
        :label="t('account_settings.csv_export_settings.labels.csv_export_delimiter')"
        type="text"
        :success-messages="success"
        :error-messages="error || toMessages(v$.csvExportDelimiter)"
        @update:model-value="callIfValid($event, update)"
      />
    </SettingsOption>
  </div>
</template>
