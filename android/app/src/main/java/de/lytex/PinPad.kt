package de.lytex

import android.content.Context
import android.graphics.Typeface
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.widget.GridLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton

/**
 * A number pad with dots for the digits typed so far, for the app lock and for setting a PIN.
 * No keyboard pops up, and no digit is shown: the screen may be watched.
 */
class PinPad(context: Context, private val onDone: (String) -> Unit) : LinearLayout(context) {
    private val digits = StringBuilder()
    private val dots = TextView(context)
    private val density = resources.displayMetrics.density
    var enabledPad = true
        set(value) { field = value; keys.forEach { it.isEnabled = value } }
    private val keys = mutableListOf<MaterialButton>()

    init {
        orientation = VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        dots.apply {
            textSize = 30f
            letterSpacing = 0.3f
            gravity = Gravity.CENTER
            minHeight = (48 * density).toInt()
            setTextColor(ContextCompat.getColor(context, R.color.text))
            contentDescription = context.getString(R.string.pin_entered, 0)
        }
        addView(dots, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        val grid = GridLayout(context).apply { columnCount = 3 }
        val labels = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "✓")
        for (label in labels) {
            val key = MaterialButton(context, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                text = label
                textSize = 24f
                typeface = Typeface.DEFAULT_BOLD
                insetTop = 0
                insetBottom = 0
                cornerRadius = (36 * density).toInt()
                contentDescription = when (label) {
                    "⌫" -> context.getString(R.string.pin_delete)
                    "✓" -> context.getString(R.string.lock_open)
                    else -> label
                }
                setOnClickListener { view ->
                    view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    press(label)
                }
            }
            if (label == "⌫") key.setOnLongClickListener { digits.clear(); draw(); true }
            val size = (72 * density).toInt()
            val margin = (8 * density).toInt()
            grid.addView(key, GridLayout.LayoutParams().apply {
                width = size; height = size; setMargins(margin, margin, margin, margin)
            })
            keys += key
        }
        addView(grid, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { topMargin = (12 * density).toInt() })
        draw()
    }

    private fun press(label: String) {
        when (label) {
            "⌫" -> if (digits.isNotEmpty()) digits.deleteCharAt(digits.length - 1)
            "✓" -> if (digits.isNotEmpty()) { val pin = digits.toString(); clear(); onDone(pin); return }
            else -> if (digits.length < MAX) digits.append(label)
        }
        draw()
    }

    fun clear() {
        digits.clear()
        draw()
    }

    private fun draw() {
        dots.text = if (digits.isEmpty()) "" else "●".repeat(digits.length)
        dots.contentDescription = context.getString(R.string.pin_entered, digits.length)
    }

    private companion object {
        const val MAX = 12
    }
}
