import DownloadRounded from '@mui/icons-material/DownloadRounded';
import { Chip, Link, Stack } from '@mui/material';
import type { ArtifactItem } from '../api/schemas';

type ExportLinkProps = { artifact: ArtifactItem };

export function ExportLink({ artifact }: ExportLinkProps) {
  return (
    <Stack direction="row" sx={{ alignItems: 'center', gap: 1, mt: 1.5 }}>
      <DownloadRounded fontSize="small" color="primary" aria-hidden />
      <Link href={artifact.url} download={artifact.fileName} underline="always">
        {`Download ${artifact.fileName}`}
      </Link>
      {artifact.draft && <Chip size="small" color="warning" variant="outlined" label="Draft" />}
    </Stack>
  );
}
