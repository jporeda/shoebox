import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BulkUpdate, type ListParams, type PhotoPage } from "./api";

export const useAlbums = () => useQuery({ queryKey: ["albums"], queryFn: api.albums });
export const useStats = () => useQuery({ queryKey: ["stats"], queryFn: api.stats });
export const useEventKinds = () => useQuery({ queryKey: ["eventKinds"], queryFn: api.eventKinds });
export const usePhotos = (list: ListParams) =>
  useQuery({ queryKey: ["photos", list], queryFn: () => api.photos(list), placeholderData: (prev) => prev });

/** Bulk edit; patches cached photo lists in place so the grid doesn't reload. */
export function useUpdatePhotos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkUpdate) => api.update(body),
    onSuccess: (res) => {
      const byId = new Map(res.photos.map((p) => [p.id, p]));
      qc.setQueriesData<PhotoPage>({ queryKey: ["photos"] }, (page) =>
        page ? { ...page, items: page.items.map((p) => byId.get(p.id) ?? p) } : page,
      );
      for (const p of res.photos) qc.invalidateQueries({ queryKey: ["photo", p.id] });
      qc.invalidateQueries({ queryKey: ["albums"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["edits"] });
      qc.invalidateQueries({ queryKey: ["eventInstances"] });
    },
  });
}

export function useUndo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (batchId?: string) => api.undo(batchId),
    onSuccess: () => qc.invalidateQueries(),
  });
}
