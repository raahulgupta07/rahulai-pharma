import { writable } from 'svelte/store';

export type DeleteConfirmOpts = {
  title?: string;
  itemName: string;
  itemType?: string;
  message?: string;
  requireType?: boolean;
};

type PendingState = (DeleteConfirmOpts & { resolve: (v: boolean) => void }) | null;

export const pendingDelete = writable<PendingState>(null);

export function confirmDelete(opts: DeleteConfirmOpts): Promise<boolean> {
  return new Promise((resolve) => {
    pendingDelete.set({
      title: opts.title || 'Delete confirmation',
      itemName: opts.itemName,
      itemType: opts.itemType || 'item',
      message: opts.message,
      requireType: opts.requireType !== false,
      resolve,
    });
  });
}

export function resolvePending(value: boolean) {
  pendingDelete.update((p) => {
    if (p) p.resolve(value);
    return null;
  });
}
