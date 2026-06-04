import { motion } from "framer-motion"

export function ThinkingDots() {
  return (
    <div className="inline-flex items-center gap-1">
      {[0, 1, 2].map((idx) => (
        <motion.span
          key={idx}
          className="size-1.5 rounded-full bg-muted-foreground/80"
          animate={{ opacity: [0.3, 1, 0.3], y: [0, -1, 0] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: idx * 0.12, ease: "easeInOut" }}
        />
      ))}
    </div>
  )
}
