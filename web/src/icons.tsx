import type { ReactElement, SVGProps } from "react"
import icebergMark from "./assets/iceberg-mark.png"

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

export function AgentIcon(): ReactElement {
  return (
    <Icon>
      <rect x="2.2" y="2" width="11.6" height="3.1" rx="0.7" fill="none" stroke="currentColor" strokeWidth="1" />
      <rect x="2.2" y="6.45" width="11.6" height="3.1" rx="0.7" fill="none" stroke="currentColor" strokeWidth="1" />
      <rect x="2.2" y="10.9" width="11.6" height="3.1" rx="0.7" fill="none" stroke="currentColor" strokeWidth="1" />
    </Icon>
  )
}

export function IcebergIcon(): ReactElement {
  return (
    <svg viewBox="0 0 32 32" width="16" height="16" aria-hidden="true">
      <image href={icebergMark} width="32" height="32" />
    </svg>
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

export function FolderIcon(props: { open: boolean }): ReactElement {
  if (props.open) {
    return (
      <Icon>
        <path
          d="M1.7 6.2 3.1 4.2h3.2l1 1.1h6.1v1.1H2.6L1.5 13h11.4l1.3-6.8H1.7z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.1"
          strokeLinejoin="round"
        />
      </Icon>
    )
  }
  return (
    <Icon>
      <path
        d="M1.8 3.7h4.1l1.15 1.25H14.2v7.7H1.8z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </Icon>
  )
}

export function FileIcon(): ReactElement {
  return (
    <Icon>
      <path
        d="M4.1 1.7h4.8l3.9 3.9v8.7H4.1z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
      <path d="M8.8 1.8v4h3.9" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
    </Icon>
  )
}

export function GearIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <path
        d="M8 1.7v1.5M8 12.8v1.5M1.7 8h1.5M12.8 8h1.5M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4 3.5 12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinecap="round"
      />
    </Icon>
  )
}

export function SignOutIcon(): ReactElement {
  return (
    <Icon>
      <path
        d="M6.4 2.8H3.4v10.4h3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
      <path
        d="M7.4 8h5.6M11 5.6 13.4 8 11 10.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Icon>
  )
}

export function PersonIcon(): ReactElement {
  return (
    <Icon>
      <circle cx="8" cy="5.3" r="2.15" fill="none" stroke="currentColor" strokeWidth="1.1" />
      <path
        d="M3.2 13.5c.7-2.5 2.5-3.7 4.8-3.7s4.1 1.2 4.8 3.7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinecap="round"
      />
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
    <svg viewBox="0 0 36 36" width="16" height="16" aria-hidden="true">
      <path
        d="M0.861099 35.3873L17.7729 18.0515C17.8788 17.9429 17.8991 17.775 17.8108 17.6516C16.782 16.2156 14.8848 15.9667 14.1814 15.002C12.0981 12.1441 11.5695 10.5266 10.6743 10.6269C10.6117 10.6339 10.556 10.6676 10.512 10.7126L4.4026 16.9752C0.887961 20.5779 0.383943 28.5103 0.291509 35.1524C0.287333 35.4525 0.651482 35.6021 0.861099 35.3873Z"
        fill="#017cee"
      />
      <path
        d="M35.4734 34.9588L18.1375 18.047C18.0289 17.941 17.861 17.9207 17.7377 18.0091C16.3017 19.0378 16.0528 20.9351 15.088 21.6384C12.2302 23.7217 10.6126 24.2504 10.7129 25.1456C10.72 25.2082 10.7536 25.2639 10.7987 25.3077L17.0613 31.4172C20.664 34.9319 28.5964 35.4359 35.2385 35.5282C35.5386 35.5326 35.6882 35.1684 35.4734 34.9588Z"
        fill="#00ad46"
      />
      <path
        d="M17.0612 31.4173C15.0932 29.4975 14.1801 25.6994 17.953 17.8671C11.8213 20.6074 9.67257 24.2094 10.7296 25.2407L17.0612 31.4173Z"
        fill="#04d659"
      />
      <path
        d="M35.0445 0.346896L18.1327 17.6827C18.0268 17.7913 18.0065 17.9592 18.0948 18.0825C19.1236 19.5186 21.0209 19.7674 21.724 20.7322C23.8075 23.59 24.3362 25.2075 25.2313 25.1074C25.2938 25.1004 25.3496 25.0666 25.3936 25.0216L31.5029 18.759C35.0177 15.1562 35.5217 7.22392 35.6141 0.58175C35.6182 0.281597 35.2541 0.132024 35.0445 0.346896Z"
        fill="#00c7d4"
      />
      <path
        d="M31.5031 18.759C29.5832 20.7269 25.7851 21.6401 17.9528 17.8671C20.693 23.9988 24.2951 26.1477 25.3263 25.0905L31.5031 18.759Z"
        fill="#11e1ee"
      />
      <path
        d="M0.432658 0.775339L17.7685 17.6871C17.8771 17.793 18.045 17.8134 18.1683 17.725C19.6043 16.6963 19.8532 14.799 20.8179 14.0957C23.6759 12.0123 25.2934 11.4837 25.193 10.5885C25.186 10.526 25.1523 10.4702 25.1074 10.4263L18.8447 4.31685C15.242 0.802203 7.30967 0.298184 0.667512 0.205751C0.367359 0.201573 0.217786 0.565722 0.432658 0.775339Z"
        fill="#e43921"
      />
      <path
        d="M18.8446 4.31675C20.8125 6.23662 21.7257 10.0346 17.9528 17.8669C24.0844 15.1267 26.2333 11.5246 25.1761 10.4934L18.8446 4.31675Z"
        fill="#ff7557"
      />
      <path
        d="M4.4028 16.9752C6.32267 15.0072 10.1207 14.0942 17.953 17.867C15.2128 11.7354 11.6107 9.58661 10.5795 10.6437L4.4028 16.9752Z"
        fill="#0cb6ff"
      />
      <path
        d="M17.9649 18.6209C18.3825 18.6157 18.7169 18.273 18.7117 17.8553C18.7065 17.4377 18.3638 17.1034 17.9462 17.1085C17.5285 17.1137 17.1942 17.4564 17.1994 17.8741C17.2045 18.2917 17.5473 18.626 17.9649 18.6209Z"
        fill="#4a4848"
      />
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
