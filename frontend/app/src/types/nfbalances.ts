import type { IgnoredAssetsHandlingType } from '@/types/asset';
import type { PaginationRequestPayload } from '@/types/common';
import { CollectionCommonFields } from '@/types/collection';
import { PriceInformation } from '@/types/prices';
import { z } from 'zod';

export const NonFungibleBalance = PriceInformation.merge(
  z.object({
    collectionName: z.string().nullable(),
    id: z.string().min(1),
    imageUrl: z.string().nullable(),
    isLp: z.boolean().nullish(),
    name: z.string().nullable(),
  }),
);

export type NonFungibleBalance = z.infer<typeof NonFungibleBalance>;

const NonFungibleBalanceArray = z.array(NonFungibleBalance);

export const NonFungibleBalancesCollectionResponse = CollectionCommonFields.extend({
  entries: NonFungibleBalanceArray,
});

export type NonFungibleBalancesCollectionResponse = z.infer<typeof NonFungibleBalancesCollectionResponse>;

export interface NonFungibleBalancesRequestPayload extends PaginationRequestPayload<NonFungibleBalance> {
  readonly name?: string;
  readonly collectionName?: string;
  readonly ignoredAssetsHandling?: IgnoredAssetsHandlingType;
}
