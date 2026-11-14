package rapairhack.ru.presentation.features

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import rapairhack.ru.databinding.ItemFeatureListBinding

data class FeatureListItem(
    val id: String,
    val title: String,
    val subtitle: String,
    val badge: String? = null
)

class FeatureListAdapter :
    ListAdapter<FeatureListItem, FeatureListAdapter.FeatureViewHolder>(DIFF_CALLBACK) {
    var onItemClick: ((FeatureListItem) -> Unit)? = null

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): FeatureViewHolder {
        val binding = ItemFeatureListBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false
        )
        return FeatureViewHolder(binding)
    }

    override fun onBindViewHolder(holder: FeatureViewHolder, position: Int) {
        val item = getItem(position)
        holder.bind(item)
        holder.itemView.setOnClickListener { onItemClick?.invoke(item) }
    }

    class FeatureViewHolder(
        private val binding: ItemFeatureListBinding
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: FeatureListItem) {
            binding.itemTitle.text = item.title
            binding.itemSubtitle.text = item.subtitle
            binding.itemBadge.text = item.badge.orEmpty()
            binding.itemBadge.visibility =
                if (item.badge.isNullOrBlank()) View.GONE else View.VISIBLE
        }
    }

    private companion object {
        val DIFF_CALLBACK = object : DiffUtil.ItemCallback<FeatureListItem>() {
            override fun areItemsTheSame(oldItem: FeatureListItem, newItem: FeatureListItem) =
                oldItem.id == newItem.id

            override fun areContentsTheSame(oldItem: FeatureListItem, newItem: FeatureListItem) =
                oldItem == newItem
        }
    }
}
