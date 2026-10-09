/** @type {import('next').NextConfig} */
const nextNextConfig = {};

/**
 * Content URLs were shareable and bookmarked, so the retired
 * `/subject/[courseId]/...` paths redirect rather than 404. Internal links were
 * all updated to the new structure; these are only for links that already
 * exist in the wild.
 */
const nextConfig = {
  ...nextNextConfig,
  async redirects() {
    return [
      {
        source: "/subject/:courseId/content/:resourceId",
        destination: "/ai-tutor/subjects/:courseId/content/:resourceId",
        permanent: false,
      },
      {
        source: "/subject/:courseId",
        destination: "/ai-tutor/subjects/:courseId",
        permanent: false,
      },
      {
        // The landing page used to link to a /demo page that never existed.
        source: "/demo",
        destination: "/dashboard",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;