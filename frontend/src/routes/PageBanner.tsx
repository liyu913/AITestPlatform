type PageBannerProps = {
  title: string
}

export default function PageBanner({ title }: PageBannerProps) {
  return (
    <div className="page-banner">
      <h1>{title}</h1>
    </div>
  )
}
