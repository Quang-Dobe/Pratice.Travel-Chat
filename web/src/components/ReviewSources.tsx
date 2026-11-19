import { Box, Link, Stack, Typography } from '@mui/material';
import type { SourceItem } from '../api/schemas';

type ReviewSourcesProps = { items: SourceItem[] };

export function ReviewSources({ items }: ReviewSourcesProps) {
  if (items.length === 0) {
    return null;
  }
  return (
    <Box
      component="section"
      aria-label="Sources"
      sx={{ mt: 1.5, pt: 1.5, borderTop: 1, borderColor: 'divider' }}
    >
      <Stack spacing={1.25}>
        {items.map((item) => (
          <Box key={item.subject}>
            <Typography variant="subtitle2" component="h4">
              {item.subject}
            </Typography>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {item.links.map((link) => (
                <li key={link.url}>
                  <Link
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    underline="always"
                    variant="body2"
                  >
                    {link.title}
                  </Link>
                </li>
              ))}
            </Box>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
