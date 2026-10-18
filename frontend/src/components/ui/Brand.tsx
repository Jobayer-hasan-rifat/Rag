export const BRAND = "Folio";

/** The product mark: a page with a folded corner and a bookmark ribbon. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--primary-solid)" />
      <path d="M10 7h8l5 5v13H10z" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M18 7v5h5" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M13 17h7M13 21h5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
