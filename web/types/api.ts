export interface SearchResult {
  unit_id: string;
  film_id: string;
  t_start: number;
  t_end: number;
  caption: string;
  keyframe_url: string;
  preview_url: string;
}

export interface SearchResponse {
  results: SearchResult[];
}
