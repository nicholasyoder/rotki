import type { useAssetIconApi } from '@/composables/api/assets/icon';
import type { Suggestion } from '@/types/filtering';
import { toSentenceCase } from '@rotki/common';
import { createCustomPinia } from '@test/utils/create-pinia';
import { mount, type VueWrapper } from '@vue/test-utils';
import { setActivePinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SuggestedItem from '@/components/table-filter/SuggestedItem.vue';
import { truncateAddress } from '@/utils/truncate';

vi.mock('@/composables/assets/retrieval', () => ({
  useAssetInfoRetrieval: vi.fn().mockReturnValue({
    assetInfo: vi.fn().mockImplementation(identifier => ({
      identifier,
      evmChain: 'ethereum',
      symbol: 'SYMBOL 2',
      isCustomAsset: false,
      name: 'Name 2',
    })),
  }),
}));

vi.mock('@/composables/api/assets/icon', () => ({
  useAssetIconApi: vi.fn().mockReturnValue({
    checkAsset: vi.fn().mockResolvedValue(404),
    assetImageUrl: vi.fn(),
  } satisfies Partial<ReturnType<typeof useAssetIconApi>>),
}));

describe('table-filter/SuggestedItem.vue', () => {
  let wrapper: VueWrapper<InstanceType<typeof SuggestedItem>>;
  const createWrapper = (props: { suggestion: Suggestion; chip?: boolean }) => {
    const pinia = createCustomPinia();
    setActivePinia(pinia);
    return mount(SuggestedItem, {
      global: {
        plugins: [pinia],
      },
      props: {
        ...props,
      },
    });
  };

  const key = 'start';
  const value = '12/12/2012';
  const assetId = 'asset_id';
  const asset = {
    identifier: assetId,
    evmChain: 'optimism',
    symbol: 'SYMBOL 1',
    isCustomAsset: false,
    name: 'Name 1',
  };

  afterEach(() => {
    wrapper.unmount();
  });

  describe('check if suggestion type is asset', () => {
    it('asset = false', () => {
      const suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key,
        value,
      };
      wrapper = createWrapper({ suggestion });

      expect(wrapper.find('span > span:nth-child(1)').text()).toBe(key);
      expect(wrapper.find('span > span:nth-child(2)').text()).toBe('=');
      expect(wrapper.find('span > span:nth-child(2)').classes().includes('comparator')).toBe(false);
      expect(wrapper.find('span > span:nth-child(3)').text()).toBe(value);
    });

    it('asset = true, send whole asset object', () => {
      const suggestion = {
        index: 0,
        total: 1,
        asset: true,
        key,
        value: asset,
      };
      wrapper = createWrapper({ suggestion });

      expect(wrapper.find('span > span:nth-child(1)').text()).toBe(key);
      expect(wrapper.find('span > div > span').text()).toBe(`${asset.symbol} (${toSentenceCase(asset.evmChain)})`);
    });

    it('asset = true, send only the id', () => {
      const suggestion = {
        index: 0,
        total: 1,
        asset: true,
        key,
        value: assetId,
      };
      wrapper = createWrapper({ suggestion });

      expect(wrapper.find('span > span:nth-child(1)').text()).toBe(key);
      expect(wrapper.find('span > div > span').text()).toBe('SYMBOL 2 (Ethereum)');
    });
  });

  it('exclude = true', () => {
    const suggestion = {
      index: 0,
      total: 1,
      asset: false,
      exclude: true,
      key,
      value,
    };
    wrapper = createWrapper({ suggestion });

    expect(wrapper.find('span > span:nth-child(2)').text()).toBe('!=');
  });

  it('chip = true', () => {
    const suggestion = {
      index: 0,
      total: 1,
      asset: false,
      key,
      value,
    };
    wrapper = createWrapper({ suggestion, chip: true });

    const comparatorClasses = wrapper.find('span > span:nth-child(2)').classes();
    expect(comparatorClasses).toContain('border-l');
    expect(comparatorClasses).toContain('border-r');
  });

  it('for boolean value', async () => {
    const suggestion = {
      index: 0,
      total: 1,
      asset: false,
      key,
      value: true,
    };
    wrapper = createWrapper({ suggestion });

    expect(wrapper.find('span').text()).toBe(`${key}=true`);

    await wrapper.setProps({ chip: true });

    expect(wrapper.find('span').text()).toBe(key);
  });

  describe('address truncation', () => {
    const validAddress = '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045';

    it('truncates plain address value', () => {
      const suggestion: Suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key: 'account',
        value: validAddress,
      };
      wrapper = createWrapper({ suggestion });

      const displayedValue = wrapper.find('span > span:nth-child(3)').text();
      expect(displayedValue).toBe(truncateAddress(validAddress, 8));
      expect(displayedValue).not.toBe(validAddress);
    });

    it('truncates address in "label (address)" format', () => {
      const label = 'vitalik.eth';
      const labelAddressValue = `${label} (${validAddress})`;
      const suggestion: Suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key: 'account',
        value: labelAddressValue,
      };
      wrapper = createWrapper({ suggestion });

      const displayedValue = wrapper.find('span > span:nth-child(3)').text();
      expect(displayedValue).toBe(`${label} (${truncateAddress(validAddress, 6)})`);
      expect(displayedValue).not.toBe(labelAddressValue);
    });

    it('does not truncate non-address values', () => {
      const plainValue = 'some-regular-text';
      const suggestion: Suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key: 'label',
        value: plainValue,
      };
      wrapper = createWrapper({ suggestion });

      const displayedValue = wrapper.find('span > span:nth-child(3)').text();
      expect(displayedValue).toBe(plainValue);
    });

    it('does not truncate "label (non-address)" format', () => {
      const labelNonAddressValue = 'My Label (some-id-123)';
      const suggestion: Suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key: 'account',
        value: labelNonAddressValue,
      };
      wrapper = createWrapper({ suggestion });

      const displayedValue = wrapper.find('span > span:nth-child(3)').text();
      expect(displayedValue).toBe(labelNonAddressValue);
    });

    it('preserves full value in title attribute for tooltip', () => {
      const label = 'vitalik.eth';
      const labelAddressValue = `${label} (${validAddress})`;
      const suggestion: Suggestion = {
        index: 0,
        total: 1,
        asset: false,
        key: 'account',
        value: labelAddressValue,
      };
      wrapper = createWrapper({ suggestion });

      const titleValue = wrapper.find('span > span:nth-child(3)').attributes('title');
      expect(titleValue).toBe(labelAddressValue);
    });
  });
});
