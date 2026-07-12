# Pintu Player video trends

These provider-agnostic JSON documents contain public aggregate ranking metadata, never video streams, accounts, personal data, or credentials. GitHub Actions updates them daily from public Trakt API endpoints using the repository secret `TRAKT_CLIENT_ID`.

Direct rankings (`trakt_official`) preserve Trakt ordering: trending, popular, and weekly watched. Composite Pintu rankings (`pintu_composite`) include new releases, Bayesian top-rated, movies of the year, and shows of the moment. Trakt supplies data only and does not provide video content or user playlists. Pintu Player is not sponsored, endorsed, or certified by Trakt.

Run **Update Pintu Video Trends** manually from GitHub Actions when an administrative refresh is needed. Generation is atomic: any failed section or validation preserves the previous complete set.
