import type { ReactElement, SVGProps } from "react"

type IconProps = SVGProps<SVGSVGElement>

function Icon({ children }: IconProps): ReactElement {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      {children}
    </svg>
  )
}

export function CatalogIcon(): ReactElement {
  return (
    <Icon>
      <ellipse cx="8" cy="4" rx="5.2" ry="2" fill="none" stroke="currentColor" strokeWidth="1" />
      <path
        d="M2.8 4v3.2c0 1.1 2.3 2 5.2 2s5.2-.9 5.2-2V4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
      <path
        d="M2.8 7.2v3.2c0 1.1 2.3 2 5.2 2s5.2-.9 5.2-2V7.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
    </Icon>
  )
}

export function SchemaIcon(): ReactElement {
  return (
    <Icon>
      <path
        d="M2 3.5h4l1 1.2H14v8H2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
    </Icon>
  )
}

export function TableIcon(): ReactElement {
  return (
    <Icon>
      <rect x="2" y="2.5" width="12" height="11" rx="1" fill="none" stroke="currentColor" strokeWidth="1" />
      <path d="M2 6h12M6 6v7.5" fill="none" stroke="currentColor" strokeWidth="1" />
    </Icon>
  )
}

export function Chevron(props: { open: boolean }): ReactElement {
  const points: string = props.open ? "3 6 8 11 13 6" : "6 3 11 8 6 13"
  return (
    <Icon>
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
        strokeLinejoin="round"
      />
    </Icon>
  )
}

export function LineageIcon(): ReactElement {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <circle cx="6" cy="6" r="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="6" cy="18" r="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="18" cy="12" r="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 7.2 16 11M8 16.8 16 13" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

export function GraphIcon(): ReactElement {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="16" cy="6" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="15" cy="16" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10 7.2 14 6.6M9.2 9.6l4.6 5M16.6 8.2 15.4 13.8" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

export function QueryIcon(): ReactElement {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <path
        d="M5 7h10M5 12h14M5 17h8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path d="m15 15 4 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="13.5" cy="13.5" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}
