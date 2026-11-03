import { useLayoutEffect, useRef, useState } from 'react'

/** Header (~55) + pagination (~56) reserved so scroll.y fits inside the fill container. */
const DEFAULT_CHROME = 111
const MIN_BODY = 240

/**
 * Measure a fill container and return Ant Design Table `scroll.y` body height.
 * Put `containerRef` on `.list-page-table` wrapping the Table.
 */
export function useTableScrollY(chromePx = DEFAULT_CHROME) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [scrollY, setScrollY] = useState(MIN_BODY)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return

    const update = () => {
      const h = el.clientHeight - chromePx
      setScrollY(Math.max(MIN_BODY, h))
    }

    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [chromePx])

  return { containerRef, scrollY }
}
