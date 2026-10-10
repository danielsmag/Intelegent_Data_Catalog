import type { ReactElement, SVGProps } from "react"

type IconProps = SVGProps<SVGSVGElement>

function Icon({ children }: IconProps): ReactElement {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      {children}
    </svg>
  )
}

export function BrandMark(): ReactElement {
  return (
    <Icon>
      <path
        d="M8 1.4 13.4 4.5v7L8 14.6 2.6 11.5v-7z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M8 5.1 11.1 6.9v3.5L8 12.2 4.9 10.4V6.9z" fill="currentColor" />
    </Icon>
  )
}

export function SearchIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="7.1" cy="7.1" r="3.3" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <path d="m9.6 9.6 3.1 3.1" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </Icon>
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

export function SourceIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="8" cy="8" r="2.1" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M8 1.8v2.2M8 12v2.2M1.8 8h2.2M12 8h2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </Icon>
  )
}

export function RefreshIcon(): ReactElement {
  return (
    <Icon>
      <path
        d="M13.2 8A5.2 5.2 0 1 1 11.6 4.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
      <path
        d="M11.4 2.2v2.6h2.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Icon>
  )
}

export function SpaceIcon(): ReactElement {
  return (
    <Icon>
      <path d="M8 1.8 13.4 4.8 8 7.8 2.6 4.8z" fill="none" stroke="currentColor" strokeWidth="1" />
      <path d="M2.6 4.8v6.2L8 14.2l5.4-3.2V4.8" fill="none" stroke="currentColor" strokeWidth="1" />
      <path d="M8 7.8v6.4" fill="none" stroke="currentColor" strokeWidth="1" />
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

export function ScopeIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="8" cy="8" r="4.7" fill="none" stroke="currentColor" strokeWidth="1" />
      <path
        d="M8 1.7v2.2M8 12.1v2.2M1.7 8h2.2M12.1 8h2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
        strokeLinecap="round"
      />
      <circle cx="8" cy="8" r="1.15" fill="currentColor" />
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

export function PanelChevron(props: { direction: "left" | "right" }): ReactElement {
  const points: string = props.direction === "left" ? "11 3 6 8 11 13" : "5 3 10 8 5 13"
  return (
    <Icon>
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Icon>
  )
}

export function MoreIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="8" cy="3.2" r="1.15" fill="currentColor" />
      <circle cx="8" cy="8" r="1.15" fill="currentColor" />
      <circle cx="8" cy="12.8" r="1.15" fill="currentColor" />
    </Icon>
  )
}

export function PlusIcon(): ReactElement {
  return (
    <Icon>
      <path
        d="M8 3.2v9.6M3.2 8h9.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
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

export function AirflowIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="3.2" cy="8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <circle cx="8" cy="3.3" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <circle cx="8" cy="12.7" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <circle cx="12.8" cy="8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <path
        d="M4.6 7.2 6.6 4.5M4.6 8.8 6.6 11.5M9.5 4.2 11.4 6.7M9.5 11.8 11.4 9.3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinecap="round"
      />
    </Icon>
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
