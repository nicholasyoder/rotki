<script lang="ts" setup>
import AmountInput from '@/components/inputs/AmountInput.vue';
import { arrayify } from '@/utils/array';

defineOptions({
  inheritAttrs: false,
});

const props = withDefaults(
  defineProps<{
    primaryValue: string;
    secondaryValue: string;
    label?: { primary?: string; secondary?: string };
    errorMessages?: {
      primary?: string | string[];
      secondary?: string | string[];
    };
    loading?: boolean;
    disabled?: boolean;
  }>(),
  {
    disabled: false,
    errorMessages: () => ({}),
    label: () => ({}),
    loading: false,
  },
);

const emit = defineEmits<{
  (e: 'update:primary-value', value: string): void;
  (e: 'update:secondary-value', value: string): void;
  (e: 'update:reversed', reversed: boolean): void;
}>();

const { errorMessages } = toRefs(props);

const primaryInput = ref<InstanceType<typeof AmountInput> | null>(null);
const secondaryInput = ref<InstanceType<typeof AmountInput> | null>(null);

const reversed = ref<boolean>(false);

function reverse() {
  const newReversed = !get(reversed);
  set(reversed, newReversed);
  emit('update:reversed', newReversed);

  nextTick(() => {
    if (!newReversed)
      get(primaryInput)?.focus();
    else get(secondaryInput)?.focus();
  });
}

function updatePrimaryValue(value: string) {
  emit('update:primary-value', value);
}

function updateSecondaryValue(value: string) {
  emit('update:secondary-value', value);
}

const aggregatedErrorMessages = computed(() => {
  const val = get(errorMessages);
  const primary = val?.primary || [];
  const secondary = val?.secondary || [];

  return [...arrayify(primary), ...arrayify(secondary)];
});

const focused = ref<boolean>(false);
</script>

<template>
  <div
    class="wrapper flex"
    :class="{
      'flex-col': !reversed,
      'flex-col-reverse': reversed,
      'focused': focused,
    }"
    v-bind="$attrs"
  >
    <AmountInput
      ref="primaryInput"
      :model-value="primaryValue"
      :disabled="reversed || disabled"
      :hide-details="!reversed"
      variant="filled"
      persistent-hint
      data-cy="primary"
      :class="`${!reversed ? 'input__enabled' : ''}`"
      :label="label.primary"
      :error-messages="aggregatedErrorMessages"
      @update:model-value="updatePrimaryValue($event)"
      @focus="focused = true"
      @blur="focused = false"
    />

    <RuiProgress
      class="relative z-[1]"
      :class="{ 'opacity-0': !loading }"
      variant="indeterminate"
      thickness="4"
      color="primary"
    />

    <AmountInput
      ref="secondaryInput"
      :model-value="secondaryValue"
      :disabled="!reversed || disabled"
      :hide-details="reversed"
      variant="filled"
      persistent-hint
      data-cy="secondary"
      :class="`${reversed ? 'input__enabled' : ''}`"
      :label="label.secondary"
      :error-messages="aggregatedErrorMessages"
      @update:model-value="updateSecondaryValue($event)"
      @focus="focused = true"
      @blur="focused = false"
    />

    <RuiButton
      icon
      class="swap-button !p-2"
      color="primary"
      data-cy="grouped-amount-input__swap-button"
      @click="reverse()"
    >
      <RuiIcon
        size="16"
        name="lu-arrow-up-down"
      />
    </RuiButton>
  </div>
</template>

<style scoped lang="scss">
.wrapper {
  @apply relative;

  > * {
    margin: -1px 0;
  }

  :deep(label) {
    @apply border-t-0 border border-[#0000006b];
    @apply rounded-b rounded-t-none #{!important};
    @apply bg-rui-grey-300 bg-opacity-40 #{!important};
  }

  /* stylelint-disable selector-class-pattern,selector-nested-pattern */

  :deep(.input__enabled) {
    label {
      @apply border-t;
      @apply border-b #{!important};
      @apply rounded-t rounded-b-none #{!important};
      @apply bg-transparent #{!important};
    }
  }
  /* stylelint-enable selector-class-pattern,selector-nested-pattern */

  &.focused {
    :deep(label) {
      @apply border-rui-primary #{!important};
      @apply border-2;
    }
  }

  :deep([class*='with-error']) {
    label {
      @apply border-rui-error #{!important};
      @apply border-2;
    }
  }

  :deep(input) {
    @apply pt-6 pb-2 #{!important};

    &:not(:placeholder-shown),
    &:focus {
      + label {
        @apply leading-7 #{!important};
      }
    }
  }
}

.swap-button {
  @apply absolute right-5 top-14 transform -translate-y-1/2 z-[1];
}

.dark {
  .wrapper {
    :deep(label) {
      @apply border-white/[0.42];
      @apply bg-rui-grey-800 bg-opacity-40 #{!important};
    }

    /* stylelint-disable selector-class-pattern,selector-nested-pattern */

    :deep(.input__enabled) {
      label {
        @apply bg-transparent #{!important};
      }
    }
    /* stylelint-enable selector-class-pattern,selector-nested-pattern */
  }
}
</style>
