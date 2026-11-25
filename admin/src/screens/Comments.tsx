/** The newest comments on songs (0022): skim them, hide what does not belong. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { get, post } from '@/api/client';
import { Button, Card, Empty, Spinner } from '@/components/ui';
import { formatDate } from '@/lib/format';

type Row = { id: number; body: string; created_at: string; track_id: number; title: string; user_id: number; first_name: string };

export function Comments() {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ['comments'], queryFn: () => get<Row[]>('/admin/comments') });
  const hide = useMutation({
    mutationFn: (id: number) => post<{ hidden: boolean }>(`/admin/comments/${id}/hide`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['comments'] }),
  });

  if (list.isLoading) return <Spinner />;
  if (!list.data?.length) return <Empty text="نظری برای بازبینی نیست." />;
  return (
    <Card>
      <table className="w-full text-[13px]">
        <thead>
          <tr>
            <th>زمان</th>
            <th>کاربر</th>
            <th>آهنگ</th>
            <th>نظر</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {list.data.map((row) => (
            <tr key={row.id}>
              <td>{formatDate(row.created_at)}</td>
              <td>
                {row.first_name} <span className="text-[var(--color-muted)]">#{row.user_id}</span>
              </td>
              <td>{row.title}</td>
              <td className="max-w-md whitespace-pre-wrap break-words">{row.body}</td>
              <td>
                <Button tone="danger" disabled={hide.isPending} onClick={() => hide.mutate(row.id)}>
                  پنهان کن
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
