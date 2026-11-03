import { useEffect, useRef } from 'react'

const BOTTOM_STICK_PX = 80

/**
 * Stick-to-bottom scroll for chat/log panels:
 * follow new content when near the bottom; keep position when the user scrolls up.
 */
export function useStickToBottom(deps: unknown[]) {
  const containerRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  function updateStickToBottom() {
    const el = containerRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickToBottomRef.current = distance <= BOTTOM_STICK_PX
  }

  function scrollToBottom() {
    const el = containerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }

  function pinToBottom() {
    stickToBottomRef.current = true
  }

  useEffect(() => {
    if (!stickToBottomRef.current) return
    scrollToBottom()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller controls when to re-scroll
  }, deps)

  return { containerRef, onScroll: updateStickToBottom, pinToBottom, scrollToBottom }
}
