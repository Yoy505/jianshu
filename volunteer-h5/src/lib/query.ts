import { QueryClient } from '@tanstack/react-query';

/** 全站单例：副作用类 mutation 一律不自动重试（服务端幂等/防重由后端与本页提交锁保证）。 */
let client: QueryClient | null = null;

export function getQueryClient(): QueryClient {
  if (!client) {
    client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 },
        mutations: { retry: false },
      },
    });
  }
  return client;
}
